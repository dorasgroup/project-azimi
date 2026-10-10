"""Ports for run-level completion tracking and fan-in aggregation coordination."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol
from uuid import UUID

from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTracker,
    RunCompletionTrackerStatus,
)


class TransientDeadlineSchedulingError(RuntimeError):
    """Raised when scheduling a deadline signal encounters a transient broker/transport error."""
    pass


class TransientControlInfrastructureError(RuntimeError):
    """A temporary control-plane dependency failure that requires durable retry."""


class UnsupportedDeadlineHorizonError(ValueError):
    """The deadline cannot be delivered within the broker-safe horizon."""


class RunCompletionTrackerRepositoryPort(Protocol):
    """Port for persisting and updating RunCompletionTracker entities."""

    async def get_or_create(
        self,
        run_id: UUID,
        deadline_at: datetime | None = None,
    ) -> RunCompletionTracker | None:
        """Idempotently fetch or create the completion tracker for a run."""
        ...

    async def get_by_run_id(self, run_id: UUID) -> RunCompletionTracker | None:
        """Fetch tracker by run_id if it exists."""
        ...

    async def record_event_and_update_counts(
        self,
        run_id: UUID,
        event_kind: str,
        entity_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> tuple[bool, RunCompletionTracker | None]:
        """Atomically record event deduplication and update counts if new.

        Returns (is_new_event, tracker).
        """
        ...

    async def transition_to_ready(
        self,
        run_id: UUID,
        expected_status: RunCompletionTrackerStatus,
        new_status: RunCompletionTrackerStatus,
        readiness_reason: str,
        cutoff_at: datetime,
        now: datetime,
    ) -> RunCompletionTracker | None:
        """Atomically transition status if current status matches expected_status."""
        ...

    async def mark_aggregation_dispatched(
        self,
        run_id: UUID,
        aggregation_id: UUID,
    ) -> RunCompletionTracker | None:
        """Record dispatched aggregation ID."""
        ...

    async def mark_analysis_ready(
        self,
        run_id: UUID,
        aggregation_id: UUID,
        ready_at: datetime,
    ) -> RunCompletionTracker | None:
        """Mark tracker as analysis_ready."""
        ...

    async def mark_cancelled(self, run_id: UUID) -> RunCompletionTracker:
        """Mark tracker as cancelled."""
        ...

    async def mark_deadline_scheduled(
        self,
        run_id: UUID,
        scheduled_at: datetime,
    ) -> RunCompletionTracker | None:
        """Record timestamp when one-shot deadline task was scheduled."""
        ...

    async def mark_control_failed(self, run_id: UUID) -> RunCompletionTracker | None:
        """Atomically close an OPEN tracker after permanent control failure."""
        ...

    async def apply_outbox_event(
        self, payload: Mapping[str, object], outbox_id: UUID, now: datetime,
    ) -> dict[str, object]:
        """Commit event deduplication, tracker decision, successor and ACK together."""
        ...

    async def close_outbox_control_failure(
        self, run_id: UUID, outbox_id: UUID, error_code: str,
    ) -> dict[str, object]:
        """Commit a permanent failure decision and consumer ACK together."""
        ...

    async def reject_invalid_outbox(self, outbox_id: UUID, error_code: str) -> None:
        """Quarantine an invalid message that cannot identify a business run."""
        ...


class DeadlinePolicyPort(Protocol):
    """Provider-neutral deadline capability required before tracker creation."""

    def validate(self, deadline_at: datetime, now: datetime) -> None: ...


class DeadlineSchedulerPort(Protocol):
    """Port for scheduling a one-shot deadline check signal to control worker."""

    def schedule_deadline_signal(
        self,
        run_id: UUID,
        tracker_id: UUID,
        deadline_at: datetime,
    ) -> None:
        """Schedule a one-shot deadline signal to the monitoring control queue."""
        ...


class RunAggregationHandoffPort(Protocol):
    """Integration port implemented by Analysis infrastructure to ensure and dispatch aggregation."""

    async def ensure_and_dispatch_aggregation(
        self,
        run_id: UUID,
        readiness_reason: str,
        cutoff_at: datetime | None,
        completion_metadata: Mapping[str, object],
    ) -> UUID | None:
        """Ensure RunAggregation and manifest exist in Analysis context and dispatch work."""
        ...


class SourceTerminalPublisherPort(Protocol):
    """Port for publishing source task terminal completion signals."""

    def publish_source_terminal(
        self,
        run_id: UUID,
        source_task_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> None:
        """Publish source terminal integration event."""
        ...
