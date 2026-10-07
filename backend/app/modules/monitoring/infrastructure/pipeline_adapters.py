"""Persistence adapters connecting application workflow ports to SQLAlchemy."""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.core.config import settings

from app.models import MonitoringJob
from app.models.enums import JobStatus, LogLevel
from app.modules.documents.ports import DocumentRepository
from app.modules.monitoring.application import (
    MarkMonitoringJobStatus,
    StartMonitoringJob,
)
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
)
from app.modules.monitoring.domain.execution import (
    MonitoringRun,
    MonitoringRunStatus,
    SourceTaskStatus,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
    SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.infrastructure.log_operations import record_system_log
from app.modules.monitoring.ports import MonitoringJobRepository
from app.shared.domain.clock import SystemClock


class SqlAlchemyPipelinePersistenceAdapter:
    """Implement persistence ports for startup, progress, cancellation, coverage, and failures."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monitoring_repositories: Callable[[AsyncSession], MonitoringJobRepository],
        document_repositories: Callable[[AsyncSession], DocumentRepository],
    ) -> None:
        self._session_factory = session_factory
        self._monitoring_repositories = monitoring_repositories
        self._document_repositories = document_repositories

    async def is_cancelled(self, job_id: UUID) -> bool:
        async with self._session_factory() as session:
            status = await session.scalar(
                select(MonitoringJob.status).where(MonitoringJob.id == job_id)
            )
            if status == JobStatus.CANCELLED:
                run_repo = SqlAlchemyMonitoringRunRepository(session)
                task_repo = SqlAlchemySourceTaskRepository(session)
                active_run = await run_repo.get_active_for_job(job_id)
                if active_run is not None:
                    cancelled_count = await task_repo.cancel_pending_tasks(active_run.id, datetime.now(UTC))
                    if cancelled_count > 0:
                        await run_repo.update_counters(
                            active_run.id,
                            terminal_delta=cancelled_count,
                            cancelled_delta=cancelled_count,
                        )
                    await run_repo.finalize_run(active_run.id, MonitoringRunStatus.CANCELLED, datetime.now(UTC))
                    await session.commit()
                return True
            return False

    async def is_missing(self, job_id: UUID) -> bool:
        async with self._session_factory() as session:
            job = await session.get(MonitoringJob, job_id)
            return job is None

    async def save_progress(self, job_id: UUID, encoded_progress: str) -> None:
        async with self._session_factory() as session:
            job = await session.get(MonitoringJob, job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                return
            job.error_message = encoded_progress
            await session.commit()

    async def log_source_failure(
        self,
        job_id: UUID,
        source_id: UUID,
        source_title: str,
        error_message: str,
        error_type: str,
    ) -> None:
        async with self._session_factory() as session:
            await record_system_log(
                session,
                level=LogLevel.ERROR,
                module="monitoring_worker",
                message="Source crawl failed; monitoring continued",
                job_id=job_id,
                context={
                    "source_id": str(source_id),
                    "source_title": source_title,
                    "error": error_message,
                    "error_type": error_type,
                },
            )
            await session.commit()

    async def source_counts(self, job_id: UUID) -> dict[UUID, int]:
        async with self._session_factory() as session:
            return await self._document_repositories(session).source_counts(job_id)

    async def log_partial_coverage(
        self,
        job_id: UUID,
        missing_titles: list[str],
        covered_titles: list[str],
    ) -> None:
        async with self._session_factory() as session:
            await record_system_log(
                session,
                level=LogLevel.WARNING,
                module="monitoring_worker",
                message="Monitoring continues with partial source coverage",
                job_id=job_id,
                context={
                    "missing_sources": missing_titles,
                    "covered_sources": covered_titles,
                },
            )
            await session.commit()

    async def transition_to_analyzing(self, job_id: UUID) -> bool:
        async with self._session_factory() as session:
            repository = self._monitoring_repositories(session)
            job = await repository.get_by_id(job_id)
            if job is None or job.status == JobStatus.CANCELLED:
                return False
            await MarkMonitoringJobStatus(repository, SystemClock()).execute(
                job, JobStatus.ANALYZING
            )
            await session.commit()
            return True

    async def log_analysis_started(self, job_id: UUID, documents: int) -> None:
        async with self._session_factory() as session:
            await record_system_log(
                session,
                level=LogLevel.INFO,
                module="monitoring_worker",
                message="Analysis engine started",
                job_id=job_id,
                context={"documents": documents},
            )
            await session.commit()

    async def log_job_completed(self, job_id: UUID, documents: int, report_id: str) -> None:
        async with self._session_factory() as session:
            await record_system_log(
                session,
                level=LogLevel.INFO,
                module="monitoring_worker",
                message="Monitoring job completed",
                job_id=job_id,
                context={"documents": documents, "report_id": report_id},
            )
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            active_run = await run_repo.get_active_for_job(job_id)
            if active_run is not None:
                await run_repo.finalize_run(active_run.id, MonitoringRunStatus.COMPLETED, datetime.now(UTC))
            await session.commit()

    async def mark_job_failed(
        self, job_id: UUID, error_message: str, error_type: str
    ) -> None:
        async with self._session_factory() as session:
            repository = self._monitoring_repositories(session)
            job = await repository.get_by_id(job_id)
            if job is not None:
                await MarkMonitoringJobStatus(repository, SystemClock()).execute(
                    job, JobStatus.FAILED, error_message=error_message, finished=True
                )
                await record_system_log(
                    session,
                    level=LogLevel.ERROR,
                    module="monitoring_worker",
                    message="Monitoring job failed",
                    job_id=job.id,
                    context={"error": error_message, "error_type": error_type},
                )
                run_repo = SqlAlchemyMonitoringRunRepository(session)
                active_run = await run_repo.get_active_for_job(job.id)
                if active_run is not None:
                    await run_repo.finalize_run(active_run.id, MonitoringRunStatus.FAILED, datetime.now(UTC))
                await session.commit()

    async def load_and_start(
        self, job_id: UUID
    ) -> tuple[str | None, MonitoringExecutionContext | None, bool]:
        async with self._session_factory() as session:
            # Existing parent first; serialize new-run startup with public cancel.
            from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
            await session.scalars(select(OrmMonitoringRun).where(
                OrmMonitoringRun.monitoring_job_id == job_id,
                OrmMonitoringRun.status.in_([MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING]),
            ).order_by(OrmMonitoringRun.id).with_for_update())
            await session.scalar(select(MonitoringJob.id).where(MonitoringJob.id == job_id).with_for_update())
            repository = self._monitoring_repositories(session)
            job = await repository.get_by_id(job_id, include_sources=True)
            if job is None:
                return "missing", None, False
            if job.status == JobStatus.CANCELLED:
                return "cancelled", None, False
            if job.status == JobStatus.COMPLETED:
                return "completed", None, False
            if job.status == JobStatus.FAILED:
                return "failed", None, False

            resume_analysis = job.status == JobStatus.ANALYZING
            resumed = job.status in {
                JobStatus.CRAWLING,
                JobStatus.PROCESSING,
                JobStatus.ANALYZING,
            }
            if job.status == JobStatus.PENDING:
                await StartMonitoringJob(
                    MarkMonitoringJobStatus(repository, SystemClock())
                ).execute(job)

            # Race-safe ensure active MonitoringRun
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            initial_mode = "distributed" if settings.monitoring_distributed_fanout_enabled else "legacy"
            active_run = await run_repo.ensure_active_run(
                job.id,
                expected_sources=len(job.sources),
                execution_mode=initial_mode,
            )
            if active_run.execution_mode == "distributed" and active_run.status == MonitoringRunStatus.PENDING:
                await run_repo.transition_status(active_run.id, MonitoringRunStatus.RUNNING, datetime.now(UTC))

            # Distributed tasks must first be inserted with complete immutable
            # specs by fanout. Placeholder rows cannot later be overwritten.
            if active_run.execution_mode == "legacy":
                indexed_sources = [(s.id, idx) for idx, s in enumerate(job.sources, start=1)]
                await task_repo.ensure_tasks_for_sources(active_run.id, indexed_sources)
            else:
                # Distributed retries resume durable fanout/control, never the
                # synchronous legacy analysis/export path.
                resume_analysis = False

            await record_system_log(
                session,
                level=LogLevel.INFO,
                module="monitoring_worker",
                message=(
                    "Monitoring job resumed after interruption"
                    if resumed
                    else "Monitoring job crawling started"
                ),
                job_id=job.id,
                context={
                    "topic": job.topic,
                    "sources": len(job.sources),
                    "resume_from": job.status.value if resumed else "pending",
                    "run_id": str(active_run.id),
                },
            )
            await session.commit()

            context = MonitoringExecutionContext(
                job_id=job.id,
                topic=job.topic,
                description=job.description,
                date_from=job.date_from_gregorian,
                date_to=job.date_to_gregorian,
                sources=[
                    {
                        "id": source.id,
                        "title": source.title,
                        "base_url": source.base_url,
                        "crawl_config": source.crawl_config or {},
                    }
                    for source in job.sources
                ],
                run_id=active_run.id,
                execution_mode=active_run.execution_mode,
            )
            return None, context, resume_analysis
