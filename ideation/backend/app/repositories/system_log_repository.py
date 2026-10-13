from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_log import SystemLog


class SystemLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, log: SystemLog) -> SystemLog:
        self.session.add(log)
        await self.session.flush()
        await self.session.refresh(log)
        return log

    async def list_logs(
        self,
        *,
        page: int,
        limit: int,
        level: str | None = None,
        event_type: str | None = None,
    ) -> tuple[list[SystemLog], int]:
        query = select(SystemLog)
        count_query = select(func.count()).select_from(SystemLog)

        if level:
            query = query.where(SystemLog.level == level)
            count_query = count_query.where(SystemLog.level == level)
        if event_type:
            query = query.where(SystemLog.event_type == event_type)
            count_query = count_query.where(SystemLog.event_type == event_type)

        total = await self.session.scalar(count_query)
        result = await self.session.execute(
            query.order_by(desc(SystemLog.created_at)).offset((page - 1) * limit).limit(limit),
        )
        return list(result.scalars().all()), int(total or 0)

    async def delete_all(self) -> int:
        result = await self.session.execute(delete(SystemLog))
        return int(result.rowcount or 0)
