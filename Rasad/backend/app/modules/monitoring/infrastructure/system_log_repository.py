"""Command persistence for structured system logs."""

from datetime import UTC, datetime, timedelta
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import JobStatus, LogLevel, MonitoringJob, SystemLog


class SqlAlchemySystemLogRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def record(self, log: SystemLog):
        self._session.add(log)
        await self._session.flush()
        return log

    async def delete_resolved(self):
        active = select(MonitoringJob.id).where(MonitoringJob.status == JobStatus.FAILED)
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        result = await self._session.execute(
            delete(SystemLog).where(
                or_(
                    SystemLog.level.notin_([LogLevel.ERROR, LogLevel.CRITICAL]),
                    SystemLog.job_id.is_not(None) & SystemLog.job_id.notin_(active),
                    SystemLog.job_id.is_(None) & (SystemLog.created_at < cutoff),
                )
            )
        )
        return result.rowcount or 0
