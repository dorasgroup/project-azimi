"""Framework-independent lifecycle rules for monitoring jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.shared.domain.errors import ConflictError, ValidationError


class MonitoringJobStatus(StrEnum):
    PENDING = "pending"
    CRAWLING = "crawling"
    PROCESSING = "processing"
    ANALYZING = "analyzing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class LifecycleState:
    """The lifecycle fields needed to decide and apply a transition."""

    status: MonitoringJobStatus
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None


class JobLifecycle:
    """Validates state transitions without knowing persistence or frameworks."""

    _ALLOWED_TRANSITIONS = {
        MonitoringJobStatus.PENDING: {
            MonitoringJobStatus.CRAWLING,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        },
        MonitoringJobStatus.CRAWLING: {
            MonitoringJobStatus.PROCESSING,
            MonitoringJobStatus.ANALYZING,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        },
        MonitoringJobStatus.PROCESSING: {
            MonitoringJobStatus.ANALYZING,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        },
        MonitoringJobStatus.ANALYZING: {
            MonitoringJobStatus.COMPLETED,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        },
        MonitoringJobStatus.FAILED: {MonitoringJobStatus.PENDING},
        MonitoringJobStatus.CANCELLED: {MonitoringJobStatus.PENDING},
        MonitoringJobStatus.COMPLETED: set(),
    }

    @classmethod
    def transition(
        cls,
        state: LifecycleState,
        target: MonitoringJobStatus,
        *,
        now: datetime,
        error_message: str | None = None,
        finished: bool = False,
    ) -> LifecycleState:
        if target == state.status:
            return state
        if target not in cls._ALLOWED_TRANSITIONS[state.status]:
            raise ConflictError(f"Cannot transition monitoring job from {state.status} to {target}")
        if target == MonitoringJobStatus.FAILED and not error_message:
            raise ValidationError("A failed monitoring job must include an error message")
        return LifecycleState(
            status=target,
            started_at=now
            if target == MonitoringJobStatus.CRAWLING and state.started_at is None
            else state.started_at,
            finished_at=now
            if finished or target in {MonitoringJobStatus.COMPLETED, MonitoringJobStatus.CANCELLED}
            else state.finished_at,
            error_message=error_message if error_message is not None else state.error_message,
        )

    @classmethod
    def reset_for_edit(cls, state: LifecycleState) -> LifecycleState:
        if state.status not in {
            MonitoringJobStatus.PENDING,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        }:
            raise ConflictError("Only failed, cancelled, or pending monitoring jobs can be edited")
        return LifecycleState(MonitoringJobStatus.PENDING, None, None, None)

    @classmethod
    def reset_for_retry(cls, state: LifecycleState) -> LifecycleState:
        if state.status not in {MonitoringJobStatus.FAILED, MonitoringJobStatus.CANCELLED}:
            raise ConflictError("Only failed or cancelled monitoring jobs can be retried")
        return LifecycleState(MonitoringJobStatus.PENDING, None, None, None)

    @classmethod
    def cancel(cls, state: LifecycleState, *, now: datetime) -> LifecycleState:
        if state.status in {
            MonitoringJobStatus.COMPLETED,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        }:
            return state
        return cls.transition(state, MonitoringJobStatus.CANCELLED, now=now)
