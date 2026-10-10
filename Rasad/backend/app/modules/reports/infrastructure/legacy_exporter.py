"""Adapters around the established PDF/DOCX generator."""

from pathlib import Path
from io import BytesIO
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from app.modules.reports.api import GenerateReportExport
from app.modules.reports.domain import (
    RenderableReport,
    RenderedReport,
    ReportExportFormat,
    ReportExportRequest,
)
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage
from app.reports.generator import ReportExportGenerator


class LegacyReportRenderer:
    """Render exactly one artifact and return bytes without owning durable storage."""

    def render(self, request: ReportExportRequest) -> RenderedReport:
        with TemporaryDirectory(prefix="rasad-report-render-") as temporary:
            generator = ReportExportGenerator(temporary)
            generator.reports_dir.mkdir(parents=True, exist_ok=True)
            if request.export_format is ReportExportFormat.PDF:
                path = generator.generate_pdf(request.report)
            elif request.export_format is ReportExportFormat.DOCX:
                path = generator.generate_docx(request.report)
            else:  # pragma: no cover - guarded by ReportExportFormat
                raise ValueError(f"Unsupported export format: {request.export_format}")
            content = path.read_bytes()
            if request.export_format is ReportExportFormat.PDF:
                if not content.startswith(b"%PDF-") or b"%%EOF" not in content[-1024:]:
                    raise ValueError("Invalid rendered PDF")
            else:
                with ZipFile(BytesIO(content)) as archive:
                    if "word/document.xml" not in archive.namelist():
                        raise ValueError("Invalid rendered DOCX")
            return RenderedReport(content=content, filename=path.name)


class ReportExportBatchAdapter:
    """Generate both durable formats through the report application use case."""

    def __init__(self, storage_root: Path | str = "/storage/reports") -> None:
        self._use_case = GenerateReportExport(
            LegacyReportRenderer(), LocalReportFileStorage(storage_root)
        )

    def generate_all(self, report: RenderableReport) -> tuple[str, str]:
        paths = tuple(
            self._use_case.execute(
                report.id,
                ReportExportRequest(report=report, export_format=export_format),
            )
            for export_format in (ReportExportFormat.PDF, ReportExportFormat.DOCX)
        )
        return str(paths[0]), str(paths[1])

    def generate(self, report: RenderableReport, export_format: str) -> Path:
        return self._use_case.execute(
            report.id,
            ReportExportRequest(
                report=report,
                export_format=ReportExportFormat(export_format),
            ),
        )


class LegacyReportExporter(ReportExportBatchAdapter):
    """Backward-compatible name; all work now flows through ports and use cases."""
