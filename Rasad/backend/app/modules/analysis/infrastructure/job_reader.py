"""SQLAlchemy read adapter for the data package required by native analysis."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import CrawledDocument, MonitoringJob


class SqlAlchemyAnalysisJobReader:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_analysis(self, job_id: UUID) -> MonitoringJob | None:
        return await self._session.scalar(
            select(MonitoringJob)
            .where(MonitoringJob.id == job_id)
            .options(
                selectinload(MonitoringJob.bot_identity),
                selectinload(MonitoringJob.requester),
                selectinload(MonitoringJob.report),
                selectinload(MonitoringJob.sources),
                selectinload(MonitoringJob.documents).selectinload(CrawledDocument.source),
            )
        )
