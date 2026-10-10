"""Reports operations ports / protocols."""

from pathlib import Path
from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.roles import UserRole
from app.schemas.common import PaginatedResponse
from app.schemas.report import ReportRead


class ReportQueries(Protocol):
    async def list_reports(
        self,
        page: int,
        page_size: int,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> PaginatedResponse[ReportRead]: ...

    async def get_report(
        self,
        report_id: UUID,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> ReportRead | None: ...


class ReportCommands(Protocol):
    async def delete_report(
        self,
        report_id: UUID,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> bool: ...

    async def export_path(
        self,
        report_id: UUID,
        export_format: str,
        *,
        user_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> Path | None: ...
