from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LogLevel, SystemLog
from app.modules.monitoring.infrastructure.system_log_repository import (
    SqlAlchemySystemLogRepository,
)
from app.modules.monitoring.ports import SystemLogReader, SystemLogRepository
from app.schemas.common import PaginatedResponse


async def record_system_log(
    session: AsyncSession,
    *,
    level: LogLevel,
    module: str,
    message: str,
    context: dict | None = None,
    request_id: UUID | None = None,
    job_id: UUID | None = None,
) -> SystemLog:
    log = SystemLog(
        level=level,
        module=module,
        message=message,
        context_json=context or {},
        request_id=request_id,
        job_id=job_id,
    )
    return await SqlAlchemySystemLogRepository(session).record(log)


class SystemLogOperationsAdapter:
    def __init__(
        self,
        read_model: SystemLogReader[PaginatedResponse[dict]],
        repository: SystemLogRepository,
    ) -> None:
        self._read_model = read_model
        self._repository = repository

    async def list_logs(
        self,
        page: int,
        page_size: int,
        *,
        level: LogLevel | None = None,
        module: str | None = None,
    ) -> PaginatedResponse[dict]:
        return await self._read_model.list(page, page_size, level=level, module=module)

    async def delete_resolved_logs(self) -> int:
        return await self._repository.delete_resolved()
