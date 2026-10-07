"""Exact-run coordinated cancellation. No job mutation, publishing or replay."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.monitoring.ports.run_cancellation import RunCancellationTransactionPort


@dataclass(frozen=True)
class RunCancellationResult:
    status: str
    sources_cancelled: int = 0
    analyses_cancelled: int = 0
    aggregations_cancelled: int = 0
    trackers_cancelled: int = 0


class CancelDistributedMonitoringRun:
    def __init__(self, transactions: RunCancellationTransactionPort) -> None:
        self._transactions = transactions

    async def execute(
        self, run_id: UUID, expected_status: MonitoringRunStatus | None = None,
        expected_updated_at: datetime | None = None,
    ) -> RunCancellationResult:
        async with self._transactions.transaction() as p:
            run = await p.runs.lock_for_cancellation(run_id)
            if run is None:
                return RunCancellationResult("not_found")
            if not run.is_distributed:
                return RunCancellationResult("not_distributed")
            if run.status not in (MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING):
                return RunCancellationResult("already_terminal")
            if ((expected_status is not None and run.status != expected_status)
                or (expected_updated_at is not None and run.updated_at != expected_updated_at)):
                return RunCancellationResult("state_changed")
            now = datetime.now(UTC)
            cancelled = run.transition_to(MonitoringRunStatus.CANCELLED, now=now)
            await p.runs.save_cancellation(cancelled)
            # Global lock order: Run -> SourceTasks -> Analyses -> Tracker -> Aggregation.
            sources = await p.sources.cancel_for_run(run_id, now)
            cancelled = cancelled.with_counters(
                terminal_delta=sources, cancelled_delta=sources, now=now)
            await p.runs.save_cancellation(cancelled)
            analyses = await p.analyses.cancel_for_run(run_id, now)
            trackers = await p.tracker.cancel_for_run(run_id, now)
            aggregations = await p.aggregations.cancel_for_run(run_id, now)
            if p.reports is not None:
                await p.reports.cancel_for_run(run_id, now)
            return RunCancellationResult("cancelled", sources, analyses, aggregations, trackers)
