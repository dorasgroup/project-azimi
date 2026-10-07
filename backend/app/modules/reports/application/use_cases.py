"""Application use cases for report rendering and persistence boundaries."""

from pathlib import Path
from uuid import UUID

from app.modules.reports.domain import ReportExportRequest
from app.modules.reports.ports import ReportFileStorage, ReportRenderer


class GenerateReportExport:
    def __init__(self, renderer: ReportRenderer, storage: ReportFileStorage) -> None:
        self._renderer = renderer
        self._storage = storage

    def execute(self, report_id: UUID, request: ReportExportRequest) -> Path:
        rendered = self._renderer.render(request)
        return self._storage.save(report_id, rendered)
