from datetime import UTC, datetime
from unittest import TestCase
from uuid import uuid4
from dataclasses import replace
from app.modules.analysis.domain.aggregate_report_snapshot import freeze_report_snapshot

from app.modules.reports.application.document_snapshot import build_document_snapshot
from app.modules.reports.domain.generation import ReportGenerationInput, ReportGenerationSpec


class DurableReportDocumentTests(TestCase):
    def test_partial_deadline_snapshot_keeps_coverage_and_provenance(self) -> None:
        spec = ReportGenerationSpec(
            title="تحلیل", created_by=uuid4(),
            date_from_jalali="1405/07/01", date_to_jalali="1405/07/08",
        )
        source_id = uuid4()
        cutoff = datetime.now(UTC)
        input_data = ReportGenerationInput(
            generation_id=uuid4(), run_id=uuid4(), aggregated_result_id=uuid4(),
            manifest_digest="a" * 64, aggregate_schema_version="1.0",
            manifest_schema_version="2.0", readiness_reason="deadline", cutoff_at=cutoff,
            expected_sources=2, successful_sources=1, failed_sources=1,
            cancelled_sources=0, analysis_successful=1, analysis_failed=1,
            analysis_cancelled=0, summary="خلاصه مستند",
            findings=({"source_id": str(source_id), "topic": "سیاست",
                       "sentiment": "neutral", "trend": "rising", "insight": "یافته"},),
            coverage_metadata={"coverage": "50%"},
            provenance_references=({"source_id": str(source_id),
                                    "source_analysis_result_id": str(uuid4())},),
            spec=spec,
        )
        input_data = replace(input_data, report_snapshot=freeze_report_snapshot(input_data.summary, input_data.findings, [{"source_id": str(source_id), "source_analysis_result_id": str(uuid4()), "summary": input_data.summary}]))
        document = build_document_snapshot(input_data)
        self.assertEqual(document.full_report["executive_summary"]["text"], "خلاصه مستند")
        metadata = document.full_report["aggregation_provenance"]
        self.assertEqual(metadata["readiness_reason"], "deadline")
        self.assertEqual(metadata["cutoff_at"], cutoff.isoformat())
        self.assertEqual(metadata["coverage"]["failed_sources"], 1)
        self.assertEqual(metadata["overall_sentiment"]["distribution"], {"neutral": 1})
        self.assertEqual(metadata["topic_distribution"], {"سیاست": 1})
        self.assertEqual(len(metadata["trends"]), 1)
        self.assertEqual(len(metadata["source_contributions"]), 1)

    def test_zero_source_report_is_honest_and_does_not_fabricate_sections(self) -> None:
        spec = ReportGenerationSpec(
            title="تحلیل", created_by=uuid4(),
            date_from_jalali="1405/07/01", date_to_jalali="1405/07/08",
        )
        input_data = ReportGenerationInput(
            generation_id=uuid4(), run_id=uuid4(), aggregated_result_id=uuid4(),
            manifest_digest="b" * 64, aggregate_schema_version="1.0",
            manifest_schema_version="2.0", readiness_reason="all_terminal", cutoff_at=None,
            expected_sources=0, successful_sources=0, failed_sources=0,
            cancelled_sources=0, analysis_successful=0, analysis_failed=0,
            analysis_cancelled=0, summary="هیچ تحلیل موفقی برای تجمیع موجود نیست.", findings=(),
            coverage_metadata={}, provenance_references=(), spec=spec,
        )
        input_data = replace(input_data, report_snapshot=freeze_report_snapshot(input_data.summary, [], []))
        document = build_document_snapshot(input_data)
        self.assertIn("هیچ تحلیل موفقی", document.summary)
        self.assertEqual(document.full_report["timeline"], [])
        self.assertEqual(document.full_report["sources_analysis"], [])
        self.assertEqual(
            document.full_report["aggregation_provenance"]["overall_sentiment"]["status"],
            "unavailable",
        )
