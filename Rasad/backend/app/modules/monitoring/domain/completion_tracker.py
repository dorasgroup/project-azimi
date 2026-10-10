"""Domain models and states for run-level completion tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class RunCompletionTrackerStatus(str, Enum):
    """Lifecycle state of a RunCompletionTracker."""
    OPEN = "open"
    READY_ALL_TERMINAL = "ready_all_terminal"
    READY_DEADLINE = "ready_deadline"
    CANCELLED = "cancelled"
    AGGREGATION_DISPATCHED = "aggregation_dispatched"
    ANALYSIS_READY = "analysis_ready"
    CONTROL_FAILED = "control_failed"

    @property
    def is_ready(self) -> bool:
        return self in (
            RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            RunCompletionTrackerStatus.READY_DEADLINE,
        )

    @property
    def is_terminal_or_dispatched(self) -> bool:
        return self in (
            RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            RunCompletionTrackerStatus.READY_DEADLINE,
            RunCompletionTrackerStatus.CANCELLED,
            RunCompletionTrackerStatus.AGGREGATION_DISPATCHED,
            RunCompletionTrackerStatus.ANALYSIS_READY,
            RunCompletionTrackerStatus.CONTROL_FAILED,
        )


@dataclass(frozen=True)
class RunCompletionTracker:
    """Immutable domain representation of a MonitoringRun's completion tracker.

    Tracks incoming terminal events for source analyses and coordinates
    readiness evaluation without blocking or polling.
    """
    id: UUID
    run_id: UUID
    status: RunCompletionTrackerStatus
    analysis_terminal_count: int
    analysis_successful_count: int
    analysis_failed_count: int
    analysis_cancelled_count: int
    deadline_at: datetime | None
    ready_at: datetime | None
    cutoff_at: datetime | None
    readiness_reason: str | None
    aggregation_id: UUID | None
    analysis_ready_at: datetime | None
    created_at: datetime
    updated_at: datetime
    deadline_scheduled_at: datetime | None = None

    def __post_init__(self) -> None:
        expected_total = (
            self.analysis_successful_count
            + self.analysis_failed_count
            + self.analysis_cancelled_count
        )
        if self.analysis_terminal_count != expected_total:
            raise ValueError(
                f"Terminal count ({self.analysis_terminal_count}) must equal sum of outcomes ({expected_total})"
            )


def evaluate_tracker_readiness(
    expected_sources: int,
    terminal_sources: int,
    successful_sources: int,
    tracker: RunCompletionTracker,
    now: datetime,
) -> tuple[bool, str | None]:
    """Pure domain evaluation of tracker readiness.

    Returns (is_ready, readiness_reason).
    """
    if tracker.status.is_terminal_or_dispatched:
        return False, None

    # 1. Natural all-terminal completion:
    # All expected sources are terminal AND all successful sources have terminal analyses
    if terminal_sources >= expected_sources and expected_sources > 0:
        if tracker.analysis_terminal_count >= successful_sources:
            return True, "all_terminal"

    # 2. Explicit deadline policy:
    if tracker.deadline_at is not None and now >= tracker.deadline_at:
        return True, "deadline"

    return False, None
