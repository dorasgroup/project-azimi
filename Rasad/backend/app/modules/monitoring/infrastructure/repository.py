"""SQLAlchemy implementation of the monitoring-job persistence port."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import BotIdentity, MonitoringJob, Source
from app.models.enums import JobStatus
from app.modules.monitoring.ports import MonitoringJobDraft


class SqlAlchemyMonitoringJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, draft: MonitoringJobDraft) -> MonitoringJob:
        sources = await self.active_sources(draft.source_ids)
        identity_id = draft.bot_identity_id or await self.default_identity_id()
        job = MonitoringJob(
            topic=draft.topic,
            description=draft.description,
            date_from_jalali=draft.date_from_jalali,
            date_to_jalali=draft.date_to_jalali,
            date_from_gregorian=draft.date_from_gregorian,
            date_to_gregorian=draft.date_to_gregorian,
            requested_by=draft.requester_id,
            bot_identity_id=identity_id,
            sources=sources,
        )
        self._session.add(job)
        await self.save()
        return job

    async def get_by_id(
        self, job_id: UUID, *, include_sources: bool = False
    ) -> MonitoringJob | None:
        options = [selectinload(MonitoringJob.sources)] if include_sources else []
        return await self._session.scalar(
            select(MonitoringJob).where(MonitoringJob.id == job_id).options(*options)
        )

    async def get_processable(
        self, job_id: UUID, *, include_sources: bool = False
    ) -> MonitoringJob | None:
        """Load a queued job only while it is eligible to start processing."""
        options = [selectinload(MonitoringJob.sources)] if include_sources else []
        return await self._session.scalar(
            select(MonitoringJob)
            .where(
                MonitoringJob.id == job_id,
                MonitoringJob.status == JobStatus.PENDING,
            )
            .options(*options)
        )

    async def get_accessible_by_id(
        self,
        job_id: UUID,
        *,
        requester_id: UUID | None,
        is_super_admin: bool,
        include_report: bool = False,
    ) -> MonitoringJob | None:
        filters = [MonitoringJob.id == job_id]
        if not is_super_admin and requester_id is not None:
            filters.append(MonitoringJob.requested_by == requester_id)
        options = [selectinload(MonitoringJob.sources)]
        if include_report:
            options.append(selectinload(MonitoringJob.report))
        return await self._session.scalar(select(MonitoringJob).where(*filters).options(*options))

    async def list_for_actor(
        self, *, page: int, page_size: int, requester_id: UUID | None, is_super_admin: bool
    ) -> tuple[list[MonitoringJob], int]:
        filters = (
            []
            if is_super_admin or requester_id is None
            else [MonitoringJob.requested_by == requester_id]
        )
        total_query = select(func.count()).select_from(MonitoringJob)
        if filters:
            total_query = total_query.where(*filters)
        total = int(await self._session.scalar(total_query) or 0)
        jobs_query = (
            select(MonitoringJob)
            .options(selectinload(MonitoringJob.sources))
            .order_by(MonitoringJob.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            jobs_query = jobs_query.where(*filters)
        jobs = list((await self._session.scalars(jobs_query)).all())
        return jobs, total

    async def default_identity_id(self) -> UUID:
        identity_id = await self._session.scalar(
            select(BotIdentity.id).where(
                BotIdentity.is_default.is_(True), BotIdentity.is_active.is_(True)
            )
        )
        if identity_id is None:
            raise ValueError("No active default bot identity is configured")
        return identity_id

    async def active_sources(self, source_ids: list[UUID]) -> list[Source]:
        sources = list(
            (
                await self._session.scalars(
                    select(Source).where(Source.id.in_(source_ids), Source.is_active.is_(True))
                )
            ).all()
        )
        if len(sources) != len(set(source_ids)):
            raise ValueError("One or more selected sources are missing or inactive")
        return sources

    async def delete(self, job: MonitoringJob) -> None:
        await self._session.delete(job)

    async def save(self) -> None:
        await self._session.flush()
