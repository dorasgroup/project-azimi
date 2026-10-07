from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from uuid import uuid4

from app.modules.reports.application import GenerateReportExport
from app.modules.reports.domain import RenderedReport, ReportExportFormat, ReportExportRequest
from app.modules.reports.infrastructure import LegacyReportRenderer


class FakeRenderer:
    def render(self, request):
        return RenderedReport(content=b"report", filename=f"report.{request.export_format.value}")


class FakeStorage:
    def __init__(self) -> None:
        self.saved = None

    def save(self, report_id, rendered):
        self.saved = (report_id, rendered.filename, rendered.content)
        return Path(f"/reports/{rendered.filename}")


class ReportsApplicationTests(TestCase):
    def test_generate_export_composes_renderer_and_storage(self) -> None:
        storage = FakeStorage()
        report_id = uuid4()
        report = SimpleNamespace(
            id=report_id,
            title="x",
            summary=None,
            full_report={},
            sources_json=[],
        )
        path = GenerateReportExport(FakeRenderer(), storage).execute(
            report_id,
            ReportExportRequest(report=report, export_format=ReportExportFormat.PDF),
        )
        self.assertEqual(path, Path("/reports/report.pdf"))
        self.assertEqual(storage.saved, (report_id, "report.pdf", b"report"))

    def test_legacy_renderer_implements_pdf_contract_without_persisting(self) -> None:
        report_id = uuid4()
        report = SimpleNamespace(
            id=report_id,
            title="گزارش آزمون",
            summary="خلاصه",
            full_report={"executive_summary": {"text": "خلاصه"}},
            sources_json=[],
        )
        rendered = LegacyReportRenderer().render(
            ReportExportRequest(report=report, export_format=ReportExportFormat.PDF)
        )
        self.assertTrue(rendered.content.startswith(b"%PDF"))
        self.assertTrue(rendered.filename.startswith(str(report_id)))
        self.assertTrue(rendered.filename.endswith(".pdf"))
