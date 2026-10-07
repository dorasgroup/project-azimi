import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase

from app.modules.analysis.domain import (
    AnalysisOutcome,
    AnalysisReportPayload,
    AnalysisRequest,
    ModelGenerationResult,
)
from app.modules.analysis.infrastructure.native_gateway import (
    NativeAnalysisError,
    NativeAnalysisGateway,
    _render,
)

FIXTURE = Path(__file__).parent / "fixtures" / "analysis_report_v1.json"


class SequencedModelGateway:
    def __init__(self, values):
        self.values = list(values)
        self.requests = []

    async def generate_json(self, request):
        self.requests.append(request)
        value = self.values.pop(0)
        return ModelGenerationResult(value, "test-provider", "test-model")


def analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        inputs={
            "topic": "موضوع آزمایشی",
            "date_range": "۱۴۰۵/۰۱/۰۱ تا ۱۴۰۵/۰۱/۰۲",
            "source_urls": json.dumps(["https://example.com/a"]),
            "source_documents": json.dumps(
                [{"title": "خبر", "url": "https://example.com/a", "content": "متن"}],
                ensure_ascii=False,
            ),
            "bot_identity": "{}",
            "analysis_requirements": "[]",
            "output_format": "json",
        },
        user_id="user-1",
    )


class NativeAnalysisGatewayTests(IsolatedAsyncioTestCase):
    async def test_native_pipeline_filters_then_validates_report(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        model = SequencedModelGateway(
            [
                {
                    "selected_documents": [{"url": "https://example.com/a"}],
                    "excluded_documents": [],
                    "evidence_map": {"ادعا": ["https://example.com/a"]},
                    "coverage_notes": "کامل",
                },
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        self.assertEqual(outcome.report.title, report["title"])
        self.assertEqual(len(model.requests), 2)
        self.assertIn("موضوع آزمایشی", model.requests[0].user_prompt)
        self.assertIn("selected_documents", model.requests[1].user_prompt)

    async def test_invalid_semantic_contract_is_rejected(self) -> None:
        model = SequencedModelGateway([{"coverage_notes": "missing selections"}])
        with self.assertRaises(NativeAnalysisError):
            await NativeAnalysisGateway(model).analyze(analysis_request())

    async def test_optional_semantic_sections_default_when_model_omits_them(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        model = SequencedModelGateway(
            [
                {"selected_documents": [{"url": "https://example.com/a"}]},
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        self.assertEqual(outcome.report.title, report["title"])
        synthesis_prompt = model.requests[1].user_prompt
        self.assertIn('"excluded_documents": []', synthesis_prompt)
        self.assertIn('"evidence_map": {}', synthesis_prompt)

    async def test_nested_json_braces_are_preserved_as_prompt_data(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        model = SequencedModelGateway(
            [
                {
                    "selected_documents": [{"url": "https://example.com/a"}],
                    "excluded_documents": [],
                    "evidence_map": {
                        "ادعا": {
                            "urls": ["https://example.com/a"],
                            "assessment": {"confidence": "بالا"},
                        }
                    },
                    "coverage_notes": {"status": {"detail": "کامل"}},
                },
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        self.assertEqual(outcome.report.title, report["title"])
        self.assertIn('"detail": "کامل"}}', model.requests[1].user_prompt)

    async def test_common_model_shape_variants_are_normalized(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        source_group = report["sources_analysis"][0]
        report["sources_analysis"] = {source_group["source_name"]: source_group}
        report["cognitive_analysis"]["key_findings"] = [
            {"finding": "یافته اول"},
            {"name": "روند", "description": "یافته دوم"},
        ]
        model = SequencedModelGateway(
            [
                {
                    "selected_documents": [{"url": "https://example.com/a"}],
                    "excluded_documents": [],
                    "evidence_map": {"ادعا": ["https://example.com/a"]},
                    "coverage_notes": "کامل",
                },
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        content = outcome.report.content
        self.assertEqual(content["sources_analysis"][0]["source_name"], "نمونه خبر")
        self.assertEqual(
            content["cognitive_analysis"]["key_findings"],
            ["یافته اول", "روند: یافته دوم"],
        )

    async def test_missing_tail_sections_use_conservative_defaults(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        report.pop("future_analysis")
        report.pop("conclusion")
        model = SequencedModelGateway(
            [
                {"selected_documents": [{"url": "https://example.com/a"}]},
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        self.assertEqual(outcome.report.content["future_analysis"]["scenarios"], [])
        self.assertEqual(
            outcome.report.content["conclusion"],
            report["executive_summary"]["text"],
        )

    async def test_nested_source_groups_and_article_aliases_are_normalized(self) -> None:
        report = json.loads(FIXTURE.read_text(encoding="utf-8"))
        article = report["sources_analysis"][0]["articles"][0]
        report["sources_analysis"] = {
            "رسانه‌های داخلی": [
                {
                    "source_name": "نمونه خبر",
                    "articles": [
                        {
                            "published_at": article["date"],
                            "headline": article["title"],
                            "description": article["summary"],
                            "link": article["url"],
                        }
                    ],
                }
            ]
        }
        model = SequencedModelGateway(
            [
                {"selected_documents": [{"url": "https://example.com/a"}]},
                report,
            ]
        )

        outcome = await NativeAnalysisGateway(model).analyze(analysis_request())

        group = outcome.report.content["sources_analysis"][0]
        self.assertEqual(group["source_name"], "نمونه خبر")
        self.assertEqual(group["articles"][0]["title"], article["title"])

    def test_render_reports_only_variables_missing_from_template_values(self) -> None:
        self.assertEqual(
            _render("داده: {{payload}}", {"payload": '{"nested": {"ok": true}}'}),
            'داده: {"nested": {"ok": true}}',
        )
        with self.assertRaisesRegex(NativeAnalysisError, "missing"):
            _render("{{present}} {{missing}}", {"present": "ok"})
