"""Report query, export, and deletion adapter."""

from pathlib import Path
from typing import TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AnalysisReport
from app.models.enums import UserRole
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage
from app.modules.reports.infrastructure.legacy_exporter import LegacyReportExporter
from app.modules.reports.infrastructure.repository import SqlAlchemyReportRepository
from app.shared.domain.errors import ValidationError


class ReportRead(TypedDict):
    id: UUID
    job_id: UUID
    title: str
    summary: str
    full_report: dict[str, object]
    sources: list[dict[str, object]]
    pdf_available: bool
    docx_available: bool
    created_at: object


def report_to_read(report: AnalysisReport) -> ReportRead:
    return {
        "id": report.id,
        "job_id": report.job_id,
        "title": report.title,
        "summary": report.summary,
        "full_report": report.full_report,
        "sources": report.sources_json,
        "pdf_available": bool(report.pdf_path),
        "docx_available": bool(report.docx_path),
        "created_at": report.created_at,
    }


class ReportQueryAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = SqlAlchemyReportRepository(session)

    async def list_reports(
        self,
        page: int,
        page_size: int,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> dict[str, object]:
        reports, total = await self._repository.list_accessible(page, page_size, user_id, role)
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": [report_to_read(report) for report in reports],
            "meta": {"page": page, "page_size": page_size, "total": total, "pages": pages},
        }

    async def get_report(
        self, report_id: UUID, *, user_id: UUID | None = None, role: UserRole | None = None
    ) -> ReportRead | None:
        report = await self._repository.get_accessible(report_id, user_id, role)
        return report_to_read(report) if report else None


class ReportCommandAdapter:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = SqlAlchemyReportRepository(session)

    async def delete_report(
        self, report_id: UUID, *, user_id: UUID | None = None, role: UserRole | None = None
    ) -> bool:
        report = await self._repository.get_accessible(report_id, user_id, role)
        if report is None:
            return False
        self._delete_file(report.pdf_path)
        self._delete_file(report.docx_path)
        await self._repository.delete(report)
        return True

    async def export_path(
        self,
        report_id: UUID,
        export_format: str,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> Path | None:
        report = await self._repository.get_accessible(report_id, user_id, role)
        if report is None:
            return None
        current_path = report.pdf_path if export_format == "pdf" else report.docx_path
        if export_format not in {"pdf", "docx"}:
            raise ValidationError(f"Unsupported export format: {export_format}")
        if not current_path or not Path(current_path).exists():
            current_path = str(LegacyReportExporter().generate(report, export_format))
            if export_format == "pdf":
                report.pdf_path = current_path
            else:
                report.docx_path = current_path
            await self._repository.save(report)
        return Path(current_path)

    @staticmethod
    def _delete_file(path: str | None) -> None:
        if not path:
            return
        try:
            LocalReportFileStorage().delete(path)
        except ValueError:
            return
