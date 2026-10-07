"""Monitoring operations ports / protocols."""

from typing import Protocol
from uuid import UUID

from app.modules.identity.domain.roles import UserRole
from app.schemas.bot_identity import BotIdentityCreate, BotIdentityRead, BotIdentityUpdate
from app.schemas.common import PaginatedResponse
from app.schemas.dashboard import DashboardSummary
from app.schemas.system_log import SystemLogRead
from app.shared.domain.logging import LogLevel


class BotIdentityQueries(Protocol):
    async def list_identities(self, page: int, page_size: int) -> PaginatedResponse[BotIdentityRead]: ...
    async def active_identity(self) -> BotIdentityRead | None: ...


class BotIdentityCommands(Protocol):
    async def create_identity(self, payload: BotIdentityCreate, creator_id: UUID) -> BotIdentityRead: ...
    async def set_active_identity(self, identity_id: UUID) -> BotIdentityRead | None: ...
    async def update_identity(self, identity_id: UUID, payload: BotIdentityUpdate) -> BotIdentityRead | None: ...
    async def delete_identity(self, identity_id: UUID) -> bool: ...


class DashboardQueries(Protocol):
    async def summary(self, *, user_id: UUID, role: UserRole) -> DashboardSummary: ...


class SystemLogOperations(Protocol):
    async def list_logs(
        self,
        page: int,
        page_size: int,
        *,
        level: LogLevel | None = None,
        module: str | None = None,
    ) -> PaginatedResponse[SystemLogRead]: ...

    async def delete_resolved_logs(self) -> int: ...
