"""Read-only SQLAlchemy projection for paginated system logs."""

from math import ceil
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import LogLevel, SystemLog
from app.schemas.common import PageMeta, PaginatedResponse


class SystemLogReadModel:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list(
        self, page: int, page_size: int, *, level: LogLevel | None = None, module: str | None = None
    ) -> PaginatedResponse[dict]:
        filters = ([SystemLog.level == level] if level else []) + (
            [SystemLog.module == module] if module else []
        )
        count = select(func.count()).select_from(SystemLog)
        query = (
            select(SystemLog)
            .order_by(SystemLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            count, query = count.where(*filters), query.where(*filters)
        total = int(await self._session.scalar(count) or 0)
        logs = list((await self._session.scalars(query)).all())
        return PaginatedResponse(
            items=[
                {
                    "id": x.id,
                    "level": x.level,
                    "module": x.module,
                    "message": x.message,
                    "context": x.context_json,
                    "request_id": x.request_id,
                    "job_id": x.job_id,
                    "created_at": x.created_at,
                }
                for x in logs
            ],
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                pages=ceil(total / page_size) if total else 0,
            ),
        )
