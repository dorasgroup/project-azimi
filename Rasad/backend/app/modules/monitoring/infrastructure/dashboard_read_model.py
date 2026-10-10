"""Read-only SQLAlchemy projection for dashboard summaries."""

from uuid import UUID
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import (
    AnalysisReport,
    BotIdentity,
    LogLevel,
    MonitoringJob,
    Source,
    SystemLog,
    User,
    UserRole,
)
from app.models.enums import JobStatus
from app.schemas.dashboard import (
    DashboardCounts,
    DashboardJobSummary,
    DashboardReportSummary,
    DashboardSummary,
)


class DashboardReadModel:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summary(self, *, user_id: UUID, role: UserRole) -> DashboardSummary:
        uf = [] if role == UserRole.SUPER_ADMIN else [MonitoringJob.requested_by == user_id]
        rf = [] if role == UserRole.SUPER_ADMIN else [AnalysisReport.created_by == user_id]
        counts = DashboardCounts(
            users=await self._count(User) if role == UserRole.SUPER_ADMIN else 1,
            sources=await self._count(Source),
            active_sources=await self._count(Source, Source.is_active.is_(True)),
            bot_identities=await self._count(BotIdentity),
            monitoring_jobs=await self._count(MonitoringJob, *uf),
            reports=await self._count(AnalysisReport, *rf),
            system_errors=await self._count(
                SystemLog, SystemLog.level.in_([LogLevel.ERROR, LogLevel.CRITICAL])
            )
            if role == UserRole.SUPER_ADMIN
            else 0,
        )
        statuses = {status: 0 for status in JobStatus}
        query = select(MonitoringJob.status, func.count()).group_by(MonitoringJob.status)
        if uf:
            query = query.where(*uf)
        for status, count in (await self._session.execute(query)).all():
            statuses[status] = int(count)
        jobs_q = select(MonitoringJob).order_by(MonitoringJob.created_at.desc()).limit(5)
        reports_q = select(AnalysisReport).order_by(AnalysisReport.created_at.desc()).limit(5)
        if uf:
            jobs_q = jobs_q.where(*uf)
        if rf:
            reports_q = reports_q.where(*rf)
        jobs = list((await self._session.scalars(jobs_q)).all())
        reports = list((await self._session.scalars(reports_q)).all())
        return DashboardSummary(
            counts=counts,
            jobs_by_status=statuses,
            recent_jobs=[
                DashboardJobSummary(
                    id=j.id,
                    topic=j.topic,
                    status=j.status,
                    created_at=j.created_at,
                    finished_at=j.finished_at,
                    error_message=j.error_message,
                )
                for j in jobs
            ],
            recent_reports=[
                DashboardReportSummary(
                    id=r.id,
                    job_id=r.job_id,
                    title=r.title,
                    summary=r.summary,
                    created_at=r.created_at,
                    source_count=len(r.sources_json or []),
                )
                for r in reports
            ],
        )

    async def _count(self, model: type, *filters) -> int:
        query = select(func.count()).select_from(model)
        if filters:
            query = query.where(*filters)
        return int(await self._session.scalar(query) or 0)
