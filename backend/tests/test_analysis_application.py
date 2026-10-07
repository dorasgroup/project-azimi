import json
from unittest import IsolatedAsyncioTestCase
from types import SimpleNamespace

from app.modules.analysis.application import RunAnalysis
from app.modules.analysis.domain import (
    AnalysisExecutiveSummary,
    AnalysisOutcome,
    AnalysisReportPayload,
    AnalysisRequest,
)
from app.modules.analysis.infrastructure.operations import AnalysisJobCoordinator


class FakeGateway:
    async def analyze(self, request):
        return AnalysisOutcome(
            report=AnalysisReportPayload(
                title=str(request.inputs["topic"]),
                executive_summary=AnalysisExecutiveSummary(text=request.user_id),
                sources=(),
                content={"topic": request.inputs["topic"]},
            )
        )


class AnalysisApplicationTests(IsolatedAsyncioTestCase):
    async def test_run_analysis_delegates_to_gateway(self) -> None:
        outcome = await RunAnalysis(FakeGateway()).execute(
            AnalysisRequest(inputs={"topic": "test"}, user_id="u-1")
        )
        self.assertEqual(outcome.report.title, "test")
        self.assertEqual(outcome.report.executive_summary.text, "u-1")

    def test_analysis_inputs_keep_monitoring_topic_exact_and_scope_locked(self) -> None:
        topic = "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"
        job = SimpleNamespace(
            topic=topic,
            description="توضیح تکمیلی برای تحلیل",
            date_from_jalali="1404/12/01",
            date_to_jalali="1404/12/09",
            bot_identity=SimpleNamespace(
                title="تحلیلگر",
                role_definition="تحلیل رسانه",
                tone="حرفه‌ای",
                analysis_style="مستند",
                cognitive_framework="شناختی",
                output_structure="گزارش",
                source_policy="منابع منتخب",
            ),
            sources=[],
        )
        coordinator = object.__new__(AnalysisJobCoordinator)

        inputs = coordinator.build_inputs(job, [])
        requirements = json.loads(inputs["analysis_requirements"])

        self.assertEqual(inputs["topic"], topic)
        self.assertTrue(any("دقیقاً و بدون هیچ تغییر" in item for item in requirements))
        self.assertTrue(any("فقط در دامنه مستقیم" in item for item in requirements))
        self.assertTrue(any("جزئی از عنوان نیست" in item for item in requirements))

    async def test_saved_report_title_is_forced_to_exact_monitoring_topic(self) -> None:
        topic = "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"
        stored_report = SimpleNamespace(
            title="",
            summary="",
            full_report={},
            sources_json=[],
            pdf_path=None,
            docx_path=None,
        )
        job = SimpleNamespace(
            topic=topic,
            report=stored_report,
            date_from_jalali="1404/12/01",
            date_to_jalali="1404/12/09",
            sources=[],
        )
        generated_report = SimpleNamespace(
            title="یک عنوان بازنویسی‌شده و نامعتبر",
            executive_summary=SimpleNamespace(text="خلاصه"),
            content={"title": "یک عنوان بازنویسی‌شده و نامعتبر"},
            sources=[],
        )

        class FakeRepository:
            async def save(self, report):
                return report

        class FakeExporter:
            def generate_all(self, report):
                return "/reports/report.pdf", "/reports/report.docx"

        coordinator = object.__new__(AnalysisJobCoordinator)
        coordinator._report_repository = FakeRepository()
        coordinator._report_exporter = FakeExporter()

        result = await coordinator.save_report(job, generated_report, [])

        self.assertEqual(result.title, topic)
        self.assertEqual(result.full_report["title"], topic)
