"""Typed report persistence and storage contracts."""

from pathlib import Path
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.reports.domain import RenderableReport, RenderedReport, ReportExportRequest


class ReportRecord(RenderableReport, Protocol):
    job_id: UUID
    pdf_path: str | None
    docx_path: str | None
    created_at: datetime


@runtime_checkable
class ReportRepository(Protocol):
    async def list_accessible(
        self,
        page: int,
        page_size: int,
        user_id: UUID | None,
        role: str | None,
    ) -> tuple[list[ReportRecord], int]: ...

    async def get_accessible(
        self, report_id: UUID, user_id: UUID | None, role: str | None
    ) -> ReportRecord | None: ...

    async def save(self, report: ReportRecord) -> None: ...

    async def delete(self, report: ReportRecord) -> None: ...

    async def delete_for_job(self, job_id: UUID) -> None: ...

    async def flush(self) -> None: ...


@runtime_checkable
class ReportFileStorage(Protocol):
    def save(self, report_id: UUID, rendered: RenderedReport) -> Path: ...

    def delete(self, path: str | Path) -> None: ...


class ReportRenderer(Protocol):
    def render(self, request: ReportExportRequest) -> RenderedReport: ...


class ReportBatchExporter(Protocol):
    def generate_all(self, report: RenderableReport) -> tuple[str, str]: ...

from app.modules.reports.ports.operations import ReportCommands, ReportQueries
