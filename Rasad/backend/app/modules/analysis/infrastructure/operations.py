from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import AnyUrl
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalysisReport, CrawledDocument, MonitoringJob
from app.models.enums import JobStatus
from app.modules.analysis.api import RunAnalysis
from app.modules.analysis.domain import AnalysisRequest
from app.modules.analysis.domain import AnalysisReportPayload
from app.modules.analysis.ports import AnalysisGateway, AnalysisJobReader, AnalysisReportWriter
from app.processing.dates import gregorian_to_jalali, publication_local_date
from app.modules.reports.ports import ReportBatchExporter
from app.shared.domain.errors import ConflictError, NotFoundError, ValidationError


def _json_string(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _url_text(value: AnyUrl | str) -> str:
    return str(value)


PERSIAN_REPORT_LANGUAGE_POLICY = (
    "All final report fields must be written in Persian. "
    "If a source document is not Persian, translate and paraphrase its evidence into Persian "
    "before using it in the analysis. Keep the original URL, source name, publication date, "
    "and source title for traceability. Do not quote long non-Persian passages in the final report."
)

MAX_ANALYSIS_DOCUMENTS = 120
MAX_DOCUMENT_EXCERPT_CHARS = 1200
ANALYSIS_BATCH_DOCUMENTS = 15
MAX_PARTIAL_REPORT_CHARS = 12000
ANALYSIS_CHECKPOINT_VERSION = 1
GREGORIAN_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")


class AnalysisCheckpointStore:
    """Durable, atomic checkpoints for resumable multi-call analysis."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or os.getenv("ANALYSIS_CHECKPOINT_DIR", "/storage/analysis-checkpoints"))

    def load(self, job_id: UUID, fingerprint: str) -> list[dict[str, Any]]:
        path = self.root / f"{job_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []
        if (
            payload.get("version") != ANALYSIS_CHECKPOINT_VERSION
            or payload.get("fingerprint") != fingerprint
            or not isinstance(payload.get("reports"), list)
        ):
            return []
        return [item for item in payload["reports"] if isinstance(item, dict)]

    def save(self, job_id: UUID, fingerprint: str, reports: list[dict[str, Any]]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{job_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "version": ANALYSIS_CHECKPOINT_VERSION,
                    "fingerprint": fingerprint,
                    "reports": reports,
                    "updated_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def clear(self, job_id: UUID) -> None:
        try:
            (self.root / f"{job_id}.json").unlink()
        except FileNotFoundError:
            pass


def _document_translation_instruction(language: str | None) -> str:
    language = (language or "unknown").lower()
    if language == "fa":
        return "Use this Persian document directly and keep the final report in Persian."
    return (
        f"This document language is {language}. Translate and paraphrase the relevant evidence "
        "into Persian before analysis and before writing any report section."
    )


def _document_excerpt(value: str | None) -> str:
    text = " ".join((value or "").split())
    if len(text) <= MAX_DOCUMENT_EXCERPT_CHARS:
        return text
    return text[:MAX_DOCUMENT_EXCERPT_CHARS].rsplit(" ", 1)[0] + " ..."


def _jalali_datetime(value: datetime | None) -> str | None:
    return gregorian_to_jalali(value) if value else None


def _localize_gregorian_dates(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            year, month, day = map(int, match.groups())
            try:
                return gregorian_to_jalali(datetime(year, month, day))
            except ValueError:
                return match.group(0)

        return GREGORIAN_DATE_RE.sub(replace, value)
    if isinstance(value, list):
        return [_localize_gregorian_dates(item) for item in value]
    if isinstance(value, dict):
        return {key: _localize_gregorian_dates(item) for key, item in value.items()}
    return value


class AnalysisJobCoordinator:
    def __init__(
        self,
        session: AsyncSession,
        gateway: AnalysisGateway,
        report_exporter: ReportBatchExporter,
        report_repository: AnalysisReportWriter,
        job_reader: AnalysisJobReader,
        checkpoint_store: AnalysisCheckpointStore | None = None,
    ) -> None:
        self.session = session
        self._gateway = gateway
        self._report_exporter = report_exporter
        self._job_reader = job_reader
        self._report_repository = report_repository
        self._checkpoint_store = checkpoint_store or AnalysisCheckpointStore()

    async def analyze_job(self, job_id: UUID) -> AnalysisReport:
        job = await self._load_job(job_id)
        if job is None:
            raise NotFoundError("Monitoring job not found")
        if job.status == JobStatus.CANCELLED:
            raise ConflictError("Cancelled monitoring jobs cannot be analyzed")
        documents = self._eligible_documents(job)
        if not documents:
            raise ValidationError("Monitoring job has no prepared documents for analysis")

        selected_documents = self._analysis_documents(documents)
        batches = [
            selected_documents[index : index + ANALYSIS_BATCH_DOCUMENTS]
            for index in range(0, len(selected_documents), ANALYSIS_BATCH_DOCUMENTS)
        ]
        fingerprint = self._analysis_fingerprint(job, selected_documents)
        partial_reports = self._checkpoint_store.load(job.id, fingerprint)
        if len(partial_reports) > len(batches):
            partial_reports = []

        # Release the database transaction before potentially long external requests.
        await self.session.commit()
        for index, batch in enumerate(batches[len(partial_reports) :], start=len(partial_reports)):
            inputs = self.build_inputs(job, batch, total_document_count=len(documents))
            inputs["analysis_requirements"] = self._batch_requirements(
                inputs["analysis_requirements"], index + 1, len(batches)
            )
            outcome = await RunAnalysis(self._gateway).execute(
                AnalysisRequest(inputs=inputs, user_id=str(job.requested_by))
            )
            partial_reports.append(dict(outcome.report.content))
            self._checkpoint_store.save(job.id, fingerprint, partial_reports)
            await self._save_analysis_progress(job, index + 1, len(batches), len(documents))

        outcome = await RunAnalysis(self._gateway).execute(
            AnalysisRequest(
                inputs=self._build_reduce_inputs(job, documents, partial_reports),
                user_id=str(job.requested_by),
            )
        )
        report = outcome.report
        analysis_report = await self.save_report(job, report, documents)
        job.status = JobStatus.COMPLETED
        job.error_message = None
        job.finished_at = datetime.now(UTC)
        await self._report_repository.flush()
        self._checkpoint_store.clear(job.id)
        return analysis_report

    def build_inputs(
        self,
        job: MonitoringJob,
        documents: list[CrawledDocument] | None = None,
        *,
        total_document_count: int | None = None,
    ) -> dict[str, Any]:
        documents = documents if documents is not None else self._eligible_documents(job)
        source_coverage = self._source_coverage(job, documents)
        unavailable_sources = [
            item["source_name"] for item in source_coverage if item["document_count"] == 0
        ]
        selected_documents = self._analysis_documents(documents)
        source_documents = [
            {
                "title": document.title or document.source.title,
                "url": document.canonical_url or document.url,
                "source_name": document.source.title,
                "published_at_jalali": _jalali_datetime(document.published_at),
                "language": document.language or "unknown",
                "translation_instruction": _document_translation_instruction(document.language),
                "content": _document_excerpt(document.clean_text),
            }
            for document in selected_documents
        ]
        language_counts: dict[str, int] = {}
        for document in documents:
            language = document.language or "unknown"
            language_counts[language] = language_counts.get(language, 0) + 1
        bot_identity = {
            "title": job.bot_identity.title,
            "role_definition": job.bot_identity.role_definition,
            "tone": job.bot_identity.tone,
            "analysis_style": job.bot_identity.analysis_style,
            "cognitive_framework": job.bot_identity.cognitive_framework,
            "output_structure": job.bot_identity.output_structure,
            "source_policy": job.bot_identity.source_policy,
        }
        topic_value = job.topic
        return {
            "topic": topic_value,
            "date_range": f"{job.date_from_jalali} تا {job.date_to_jalali}",
            "bot_identity": _json_string(bot_identity),
            "source_documents": _json_string(source_documents),
            "source_urls": _json_string([document["url"] for document in source_documents]),
            "analysis_requirements": _json_string(
                [
                    PERSIAN_REPORT_LANGUAGE_POLICY,
                    (
                        "عنوان گزارش در فیلد title باید دقیقاً و بدون هیچ تغییر، بازنویسی، "
                        f"زیرعنوان یا افزودن تاریخ برابر با موضوع رصد باشد: {job.topic}"
                    ),
                    (
                        "تمام تحلیل، یافته‌ها، روایت‌ها و جمع‌بندی باید فقط در دامنه مستقیم "
                        f"موضوع رصد «{job.topic}» باشند؛ از ساختن موضوع جایگزین یا چارچوب مسلط "
                        "جدید خودداری کن و محور فرعی را فقط با شاهد مرتبط وارد کن."
                    ),
                    *(
                        [
                            "توضیحات تکمیلی رصد فقط برای فهم دامنه است و جزئی از عنوان نیست: "
                            + job.description
                        ]
                        if job.description
                        else []
                    ),
                    "تمام تاریخ‌ها در title، monitoring_scope، متن گزارش، منابع و جمع‌بندی باید فقط شمسی باشند؛ تاریخ میلادی ننویس.",
                    "گزارش باید جامع، تفصیلی، مستند و مناسب ارائه به مخاطب حرفه‌ای باشد، نه خلاصه کوتاه.",
                    "برای هر منبع انتخابی که سند معتبر دارد، حداقل یک مشاهده یا شاهد مشخص در تحلیل بیاور.",
                    "یافته‌های کلیدی حداقل ۸ مورد باشند مگر اینکه شواهد کمتر از این مقدار باشد.",
                    "تحلیل شناختی باید چندپاراگرافی و شامل روند زمانی، روایت‌ها، بازیگران، تغییر لحن و الگوی رسانه‌ای باشد.",
                    "در conclusion توضیح بده شواهد چه تصویری از کل بازه رصد می‌سازند و محدودیت‌های داده چیست.",
                    (
                        f"برای تحلیل، {len(selected_documents)} سند از {total_document_count or len(documents)} سند معتبر داخل بازه "
                        f"و از {len(source_coverage)} منبع انتخاب شده در بسته شواهد آمده است."
                    ),
                    "Source language counts: " + _json_string(language_counts),
                    "Translate every non-Persian source into Persian conceptually before extracting findings, narratives, actors, and conclusions.",
                    "The final JSON values must be Persian text, except URLs, dates, and unavoidable proper names.",
                    "تفکیک واقعیت از تفسیر",
                    "ذکر شواهد برای روایت‌ها و بازیگران",
                    "خودداری از ادعای قطعی عملیات روانی",
                    "استفاده از اسناد همه منابع انتخابی و ذکر همه منابع در خروجی",
                    "نادیده گرفتن هر سندی که تاریخ آن خارج از بازه رصد است",
                    (
                        "این منابع بررسی شدند اما سند معتبر داخل بازه نداشتند؛ "
                        "نباید به‌عنوان منبع استفاده‌شده معرفی شوند: "
                        + "، ".join(unavailable_sources)
                        if unavailable_sources
                        else "همه منابع انتخابی حداقل یک سند معتبر داخل بازه دارند"
                    ),
                ]
            ),
            "output_format": "json",
        }

    @staticmethod
    def _batch_requirements(value: object, batch_number: int, batch_count: int) -> str:
        requirements = json.loads(str(value))
        requirements.extend(
            [
                f"این مرحله تحلیل جزئی دسته {batch_number} از {batch_count} است.",
                "فقط شواهد همین دسته را تحلیل کن و یک گزارش JSON کامل و معتبر بساز؛ ادغام نهایی جداگانه انجام می‌شود.",
            ]
        )
        return _json_string(requirements)

    def _build_reduce_inputs(
        self,
        job: MonitoringJob,
        documents: list[CrawledDocument],
        partial_reports: list[dict[str, Any]],
    ) -> dict[str, Any]:
        inputs = self.build_inputs(job, documents[:1], total_document_count=len(documents))
        synthetic_documents = []
        for index, report in enumerate(partial_reports, start=1):
            synthetic_documents.append(
                {
                    "title": f"تحلیل جزئی دسته {index}",
                    "url": f"https://internal.rasad.local/analysis/{job.id}/batch/{index}",
                    "source_name": "سامانه رصد - تحلیل میانی",
                    "published_at_jalali": None,
                    "language": "fa",
                    "translation_instruction": "این متن فارسی است.",
                    "content": _json_string(report)[:MAX_PARTIAL_REPORT_CHARS],
                }
            )
        inputs["source_documents"] = _json_string(synthetic_documents)
        inputs["source_urls"] = _json_string([item["url"] for item in synthetic_documents])
        requirements = json.loads(str(inputs["analysis_requirements"]))
        requirements.extend(
            [
                "این مرحله ادغام نهایی تحلیل‌های جزئی است.",
                "هم‌پوشانی‌ها را حذف کن، تعارض شواهد را صریح نگه دار و یک گزارش نهایی منسجم و جامع بساز.",
                "منابع واقعی هر تحلیل جزئی را حفظ کن و URL داخلی تحلیل میانی را در فهرست منابع نهایی نیاور.",
            ]
        )
        inputs["analysis_requirements"] = _json_string(requirements)
        return inputs

    @staticmethod
    def _analysis_fingerprint(job: MonitoringJob, documents: list[CrawledDocument]) -> str:
        digest = hashlib.sha256()
        digest.update(str(job.id).encode())
        digest.update(job.topic.encode("utf-8"))
        for document in documents:
            digest.update(str(document.id).encode())
            digest.update(str(document.canonical_url or document.url).encode("utf-8"))
            digest.update((document.clean_text or "").encode("utf-8"))
        return digest.hexdigest()

    async def _save_analysis_progress(
        self,
        job: MonitoringJob,
        completed_batches: int,
        total_batches: int,
        document_count: int,
    ) -> None:
        from app.modules.monitoring.domain.progress import (
            MonitoringProgressSnapshot,
            decode_progress,
            encode_progress,
        )

        previous = decode_progress(job.error_message)
        job.error_message = encode_progress(
            MonitoringProgressSnapshot(
                stage="analyzing",
                percent=70 + round(24 * completed_batches / max(1, total_batches)),
                sources_completed=previous.sources_completed if previous else len(job.sources),
                sources_total=previous.sources_total if previous else len(job.sources),
                pages_processed=previous.pages_processed if previous else document_count,
                documents_prepared=document_count,
                eta_seconds=None,
                message=f"تحلیل دسته‌ای: {completed_batches} از {total_batches} دسته تکمیل شد",
            )
        )
        await self.session.commit()

    async def save_report(
        self,
        job: MonitoringJob,
        report: AnalysisReportPayload,
        documents: list[CrawledDocument] | None = None,
    ) -> AnalysisReport:
        report_data = _localize_gregorian_dates(dict(report.content))
        report_data["title"] = job.topic
        documents = documents if documents is not None else self._eligible_documents(job)
        source_coverage = self._source_coverage(job, documents)
        report_data["monitoring_range_jalali"] = {
            "from": job.date_from_jalali,
            "to": job.date_to_jalali,
        }
        sources_by_url = {
            str(document.canonical_url or document.url): {
                "title": document.title or document.source.title,
                "url": str(document.canonical_url or document.url),
                "source_name": document.source.title,
                "published_at_jalali": _jalali_datetime(document.published_at),
            }
            for document in documents
        }
        for source in report.sources:
            url = _url_text(source.url)
            sources_by_url.setdefault(
                url,
                {
                    "title": source.title,
                    "url": url,
                    "source_name": source.source_name,
                    "published_at_jalali": None,
                },
            )
        sources = list(sources_by_url.values())
        report_data["sources"] = sources
        report_data["source_coverage"] = source_coverage
        analysis_report = job.report
        if analysis_report is None:
            analysis_report = AnalysisReport(
                job=job,
                creator=job.requester,
                title=job.topic,
                summary=_localize_gregorian_dates(report.executive_summary.text),
                full_report=report_data,
                sources_json=sources,
            )
            await self._report_repository.save(analysis_report)
        else:
            analysis_report.title = job.topic
            analysis_report.summary = _localize_gregorian_dates(report.executive_summary.text)
            analysis_report.full_report = report_data
            analysis_report.sources_json = sources
        await self._report_repository.save(analysis_report)
        pdf_path, docx_path = self._report_exporter.generate_all(analysis_report)
        analysis_report.pdf_path = pdf_path
        analysis_report.docx_path = docx_path
        await self._report_repository.save(analysis_report)
        return analysis_report

    async def _load_job(self, job_id: UUID) -> MonitoringJob | None:
        return await self._job_reader.get_for_analysis(job_id)

    @staticmethod
    def _eligible_documents(job: MonitoringJob) -> list[CrawledDocument]:
        return [
            document
            for document in job.documents
            if document.clean_text
            and document.published_at is not None
            and job.date_from_gregorian
            <= publication_local_date(document.published_at)
            <= job.date_to_gregorian
        ]

    @staticmethod
    def _analysis_documents(documents: list[CrawledDocument]) -> list[CrawledDocument]:
        by_source: dict[UUID, list[CrawledDocument]] = {}
        for document in sorted(
            documents,
            key=lambda item: (
                item.source.title,
                -(item.published_at.timestamp() if item.published_at else 0),
                -(item.matched_score or 0),
            ),
        ):
            by_source.setdefault(document.source_id, []).append(document)
        per_source_limit = max(6, min(15, MAX_ANALYSIS_DOCUMENTS // max(1, len(by_source))))
        selected: list[CrawledDocument] = []
        for source_documents in by_source.values():
            selected.extend(source_documents[:per_source_limit])
        if len(selected) <= MAX_ANALYSIS_DOCUMENTS:
            return selected
        return sorted(
            selected,
            key=lambda item: (
                -(item.matched_score or 0),
                -(item.published_at.timestamp() if item.published_at else 0),
            ),
        )[:MAX_ANALYSIS_DOCUMENTS]

    @staticmethod
    def _source_coverage(
        job: MonitoringJob,
        documents: list[CrawledDocument],
    ) -> list[dict[str, Any]]:
        counts: dict[UUID, int] = {}
        for document in documents:
            counts[document.source_id] = counts.get(document.source_id, 0) + 1
        return [
            {
                "source_name": source.title,
                "base_url": source.base_url,
                "document_count": counts.get(source.id, 0),
                "status": "used"
                if counts.get(source.id, 0) > 0
                else "checked_without_valid_document",
                "status_message": source.last_status_message,
            }
            for source in job.sources
        ]
