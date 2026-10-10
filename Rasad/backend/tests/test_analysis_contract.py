import json
from pathlib import Path
from unittest import TestCase

from pydantic import ValidationError

from app.modules.analysis.domain import AnalysisReportDocument
from app.modules.analysis.prompts import (
    ANALYSIS_PROMPT_VERSION,
    REPORT_SYNTHESIS_SYSTEM_PROMPT,
    REPORT_SYNTHESIS_USER_TEMPLATE,
    SEMANTIC_FILTER_SYSTEM_PROMPT,
    SEMANTIC_FILTER_USER_TEMPLATE,
)
FIXTURE = Path(__file__).parent / "fixtures" / "analysis_report_v1.json"


class AnalysisReportContractTests(TestCase):
    def test_golden_report_matches_provider_neutral_contract(self) -> None:
        report = AnalysisReportDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8"))

        self.assertEqual(report.title, "تحلیل مذاکرات ایران و آمریکا")
        self.assertEqual(str(report.sources[0].url), "https://example.com/news/1")
        self.assertEqual(report.as_content()["executive_summary"]["metrics"][0]["value"], "۲")

    def test_empty_model_output_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisReportDocument.model_validate({})

    def test_numeric_display_metric_is_normalized_to_text(self) -> None:
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["executive_summary"]["metrics"][0]["value"] = 1

        report = AnalysisReportDocument.model_validate(payload)

        self.assertEqual(report.executive_summary.metrics[0].value, "1")

    def test_json_schema_keeps_all_report_sections_required(self) -> None:
        required = set(AnalysisReportDocument.model_json_schema()["required"])

        self.assertEqual(
            required,
            {
                "title",
                "executive_summary",
                "cognitive_analysis",
                "future_analysis",
                "conclusion",
            },
        )


class AnalysisPromptBaselineTests(TestCase):
    def test_prompt_version_and_inputs_are_explicit(self) -> None:
        self.assertEqual(ANALYSIS_PROMPT_VERSION, "1.0.0")
        for variable in ("topic", "date_range", "source_urls", "source_documents"):
            self.assertIn("{{" + variable + "}}", SEMANTIC_FILTER_USER_TEMPLATE)
        for variable in (
            "topic",
            "date_range",
            "bot_identity",
            "analysis_requirements",
            "output_format",
            "semantic_evidence",
        ):
            self.assertIn("{{" + variable + "}}", REPORT_SYNTHESIS_USER_TEMPLATE)

    def test_prompts_preserve_evidence_and_report_guards(self) -> None:
        self.assertIn("URL واقعی", SEMANTIC_FILTER_SYSTEM_PROMPT)
        self.assertIn("هیچ منبع یا ادعایی نسازید", REPORT_SYNTHESIS_SYSTEM_PROMPT)
        for key in json.loads(FIXTURE.read_text(encoding="utf-8")):
            self.assertIn(key, AnalysisReportDocument.model_json_schema()["properties"])
