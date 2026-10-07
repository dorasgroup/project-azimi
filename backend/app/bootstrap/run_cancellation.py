"""Composition root: one fresh session/transaction across module-owned adapters."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.analysis.infrastructure.cancellation_adapters import (
    CancelSourceAnalysesAdapter, CancelRunAggregationsAdapter,
)
from app.modules.monitoring.application.cancel_distributed_run import CancelDistributedMonitoringRun
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository, SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.infrastructure.tracker_cancellation import CancelCompletionTrackerAdapter
from app.modules.monitoring.ports.run_cancellation import CancellationParticipants
from app.modules.reports.infrastructure.cancellation_adapter import CancelReportGenerationsAdapter


class SqlAlchemyRunCancellationTransaction:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[CancellationParticipants]:
        async with self._session_factory() as session, session.begin():
            yield CancellationParticipants(
                runs=SqlAlchemyMonitoringRunRepository(session),
                sources=SqlAlchemySourceTaskRepository(session),
                analyses=CancelSourceAnalysesAdapter(session),
                tracker=CancelCompletionTrackerAdapter(session),
                aggregations=CancelRunAggregationsAdapter(session),
                reports=CancelReportGenerationsAdapter(session),
            )


def provide_run_cancellation(
    session_factory: async_sessionmaker[AsyncSession],
) -> CancelDistributedMonitoringRun:
    return CancelDistributedMonitoringRun(SqlAlchemyRunCancellationTransaction(session_factory))


class RequestSessionRunCancellationTransaction:
    """Participate in the API transaction; never commit in a sub-operation."""
    def __init__(self, session) -> None:
        self._session = session

    @asynccontextmanager
    async def transaction(self):
        s = self._session
        yield CancellationParticipants(
            SqlAlchemyMonitoringRunRepository(s), SqlAlchemySourceTaskRepository(s),
            CancelSourceAnalysesAdapter(s), CancelCompletionTrackerAdapter(s),
            CancelRunAggregationsAdapter(s), CancelReportGenerationsAdapter(s),
        )


class CancelJobRunsAdapter:
    """After owner authorization, close exact active runs and lock fresh job state.

    Lock order is Run -> children -> Job. A new-run race refuses the operation
    instead of acquiring a Run lock while holding a Job lock.
    """
    def __init__(self, session) -> None:
        self._session = session

    async def close_active_for_job(self, job_id) -> None:
        from sqlalchemy import select
        from app.models.monitoring_run import MonitoringRun
        from app.models.monitoring_job import MonitoringJob
        from app.modules.monitoring.domain.execution import MonitoringRunStatus
        from app.shared.domain.errors import ConflictError
        s = self._session
        active = select(MonitoringRun).where(
            MonitoringRun.monitoring_job_id == job_id,
            MonitoringRun.status.in_([MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING]),
        ).order_by(MonitoringRun.id)
        runs = list((await s.scalars(active.with_for_update())).all())
        coordinator = CancelDistributedMonitoringRun(RequestSessionRunCancellationTransaction(s))
        for run in runs:
            if run.execution_mode == "distributed":
                await coordinator.execute(run.id)
        await s.scalar(select(MonitoringJob).where(MonitoringJob.id == job_id)
                       .with_for_update().execution_options(populate_existing=True))
        current = list((await s.scalars(active)).all())
        if any(r.id not in {known.id for known in runs} for r in current):
            raise ConflictError("Monitoring run started concurrently; retry cancellation")
