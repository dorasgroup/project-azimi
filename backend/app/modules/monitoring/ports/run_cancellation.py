"""Narrow lifecycle closure ports. Every adapter participates in the same UoW."""

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.monitoring.domain.execution import MonitoringRun


class RunCancellationPort(Protocol):
    async def lock_for_cancellation(self, run_id: UUID) -> MonitoringRun | None: ...
    async def save_cancellation(self, run: MonitoringRun) -> None: ...


class CancelSourceTasksForRunPort(Protocol):
    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int: ...


class CancelSourceAnalysesForRunPort(Protocol):
    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int: ...


class CancelRunAggregationsForRunPort(Protocol):
    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int: ...


class CancelCompletionTrackerPort(Protocol):
    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int: ...


class CancelReportGenerationsPort(Protocol):
    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int: ...


@dataclass(frozen=True)
class CancellationParticipants:
    runs: RunCancellationPort
    sources: CancelSourceTasksForRunPort
    analyses: CancelSourceAnalysesForRunPort
    tracker: CancelCompletionTrackerPort
    aggregations: CancelRunAggregationsForRunPort
    reports: CancelReportGenerationsPort | None = None


class RunCancellationTransactionPort(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[CancellationParticipants]: ...
