"""Adapt the pinned aggregation presentation snapshot to the existing renderer."""

from copy import deepcopy
from dataclasses import dataclass
from uuid import UUID

from app.modules.reports.domain.generation import ReportGenerationInput


@dataclass(frozen=True)
class ReportDocumentSnapshot:
    id: UUID
    title: str
    summary: str
    full_report: dict[str, object]
    sources_json: list[dict[str, object]]


def build_document_snapshot(source: ReportGenerationInput) -> ReportDocumentSnapshot:
    snapshot = source.report_snapshot
    if not isinstance(snapshot, dict) or snapshot.get("snapshot_version") != "1.0":
        raise ValueError("Pinned aggregate lacks supported report presentation snapshot")
    if not {"overall_sentiment", "topic_distribution", "trends", "contradictions", "source_contributions", "availability"} <= snapshot.keys():
        raise ValueError("Pinned aggregate report consumer contract is incomplete")
    document = snapshot.get("document")
    required = {"executive_summary", "timeline", "sources_analysis", "cognitive_analysis", "future_analysis", "conclusion"}
    if not isinstance(document, dict) or not required <= document.keys():
        raise ValueError("Pinned report document is incomplete")
    summary = document.get("executive_summary")
    if not isinstance(summary, dict) or summary.get("text") != source.summary:
        raise ValueError("Pinned report summary differs from aggregate")
    sources = snapshot.get("sources")
    if not isinstance(sources, list) or not all(isinstance(s, dict) for s in sources):
        raise ValueError("Pinned report sources are invalid")
    coverage = {
        **dict(source.coverage_metadata),
        "expected_sources": source.expected_sources,
        "successful_sources": source.successful_sources,
        "failed_sources": source.failed_sources,
        "cancelled_sources": source.cancelled_sources,
        "analysis_successful": source.analysis_successful,
        "analysis_failed": source.analysis_failed,
        "analysis_cancelled": source.analysis_cancelled,
    }
    full_report = deepcopy(document)
    full_report["executive_summary"]["metrics"] = [
        {"label": "منابع مورد انتظار", "value": str(source.expected_sources)},
        {"label": "منابع پردازش‌شده", "value": str(source.successful_sources)},
        {"label": "منابع ناموفق", "value": str(source.failed_sources)},
        {"label": "تحلیل موفق", "value": str(source.analysis_successful)},
    ]
    full_report["title"] = source.spec.title
    full_report["sources"] = deepcopy(sources)
    full_report["source_coverage"] = coverage
    if coverage.get('crawl_sources'):
        full_report['crawl_coverage'] = deepcopy(coverage['crawl_sources'])
        limitations = [str(item.get('source_name', '')) for item in coverage['crawl_sources'] if item.get('status') != 'success']
        if limitations:
            full_report['conclusion'] += '\n\nمحدودیت پوشش: دریافت صفحات منتخب این منابع کامل نبود یا مدرک قابل دریافت نداشتند: ' + '، '.join(limitations) + '. نتیجه فقط بر شواهد معتبر دریافت‌شده متکی است و پوشش کامل همه منابع ادعا نمی‌شود.'
    full_report["monitoring_range_jalali"] = {"from": source.spec.date_from_jalali, "to": source.spec.date_to_jalali}
    full_report["aggregation_provenance"] = {
        "aggregate_schema_version": source.aggregate_schema_version,
        "manifest_schema_version": source.manifest_schema_version,
        "manifest_digest": source.manifest_digest,
        "aggregated_result_id": str(source.aggregated_result_id),
        "report_spec_digest": source.spec.digest,
        "report_schema_version": source.spec.schema_version,
        "template_version": source.spec.template_version,
        "readiness_reason": source.readiness_reason,
        "cutoff_at": source.cutoff_at.isoformat() if source.cutoff_at else None,
        "coverage": coverage,
        "provenance_references": deepcopy(list(source.provenance_references)),
        **{key: deepcopy(snapshot[key]) for key in (
            "overall_sentiment", "topic_distribution", "trends", "contradictions",
            "source_contributions", "availability",
        ) if key in snapshot},
    }
    return ReportDocumentSnapshot(source.generation_id, source.spec.title, source.summary, full_report, deepcopy(sources))
