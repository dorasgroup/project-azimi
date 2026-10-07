"""Real source/aggregation adapters feed the real exporter without live tables."""

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

import pytest
from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec
from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
from app.modules.reports.application.document_snapshot import build_document_snapshot
from app.modules.reports.domain.generation import ReportGenerationInput, ReportGenerationSpec
from app.modules.reports.domain.contracts import ReportExportFormat, ReportExportRequest
from app.modules.reports.infrastructure.legacy_exporter import LegacyReportRenderer


@pytest.mark.asyncio
async def test_structured_source_to_pinned_aggregate_to_both_exports():
    source_id, result_id = uuid4(), uuid4()
    url = "https://example.invalid/negotiations/1"
    class Gateway:
        calls = 0
        async def generate_json(self, request):
            self.calls += 1
            assert self.calls == 1, "Export must never call an analysis provider"
            return SimpleNamespace(content={
                "summary": "رسانه مذاکرات را با تاکید بر اختلاف مواضع پوشش داده است.",
                "findings": [{"title": "اختلاف مواضع", "explanation": "گفت‌وگوها ادامه دارد اما اختلاف باقی است.",
                              "evidence": "گفت‌وگوها ادامه دارد", "document_url": url,
                              "topic": "مذاکرات", "sentiment": "neutral", "trend": "ادامه گفتگو",
                              "contradiction": "تفاوت روایت درباره توافق"}],
                "evidence_map": {url: "اظهارات مستند دو طرف"},
            })
    gateway = Gateway()
    document = SimpleNamespace(id=uuid4(), source_task_id=uuid4(), run_id=uuid4(), source_id=source_id,
        url=url, canonical_url=url, title="گزارش مذاکرات", clean_text="گفت‌وگوها ادامه دارد.",
        published_at=datetime(2026, 9, 30, tzinfo=UTC), language="fa", matched_score=1, content_hash="c" * 64)
    source_result = await SingleSourceAnalyzer(gateway).analyze_source(SourceAnalysisSpec(source_title="منبع آزمون", source_url="https://example.invalid", topic="مذاکرات"), [document], "مذاکرات")
    aggregate = await RunAggregatorAdapter().aggregate({}, [{
        "source_id": str(source_id), "source_analysis_result_id": str(result_id),
        "summary": source_result.summary, "findings": source_result.findings,
        "evidence_map": source_result.evidence_map, "raw_result": source_result.raw_result,
        "input_digest": source_result.input_digest,
    }], {"coverage": "partial"})
    pinned = ReportGenerationInput(generation_id=uuid4(), run_id=document.run_id, aggregated_result_id=uuid4(),
        manifest_digest="a" * 64, aggregate_schema_version="1.0", manifest_schema_version="2.0",
        readiness_reason="deadline", cutoff_at=document.published_at, expected_sources=2, successful_sources=1,
        failed_sources=1, cancelled_sources=0, analysis_successful=1, analysis_failed=0, analysis_cancelled=0,
        summary=aggregate["summary"], findings=tuple(aggregate["findings"]), coverage_metadata=aggregate["coverage_metadata"],
        provenance_references=tuple(aggregate["provenance_references"]),
        spec=ReportGenerationSpec(title="تحلیل مذاکرات", created_by=uuid4(), date_from_jalali="1405/07/01", date_to_jalali="1405/07/08"),
        report_snapshot=aggregate["raw_result"]["report_snapshot"])
    # The export consumer has only the persisted contract, not source state.
    del document, source_result, aggregate
    exported = build_document_snapshot(pinned)
    metadata = exported.full_report["aggregation_provenance"]
    assert metadata["overall_sentiment"]["distribution"] == {"neutral": 1}
    assert metadata["topic_distribution"] == {"مذاکرات": 1}
    assert metadata["trends"] and metadata["contradictions"]
    assert metadata["source_contributions"][0]["source_analysis_result_id"] == str(result_id)
    assert metadata["coverage"]["expected_sources"] == 2
    assert exported.sources_json[0]["url"] == url
    renderer = LegacyReportRenderer()
    pdf = renderer.render(ReportExportRequest(exported, ReportExportFormat.PDF))
    docx = renderer.render(ReportExportRequest(exported, ReportExportFormat.DOCX))
    assert pdf.content.startswith(b"%PDF-") and b"%%EOF" in pdf.content[-1024:]
    with ZipFile(BytesIO(docx.content)) as archive:
        xml = archive.read("word/document.xml").decode()
    for content in ("اختلاف مواضع", "منبع آزمون", "اظهارات مستند", "گزارش مذاکرات"):
        assert content in xml
    assert gateway.calls == 1
