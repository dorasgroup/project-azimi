"""Durable decisions for monitoring control events, independent of Celery."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Mapping, Protocol
from uuid import UUID

from app.modules.monitoring.ports.completion import (
    RunCompletionTrackerRepositoryPort,
    TransientDeadlineSchedulingError,
    UnsupportedDeadlineHorizonError,
)
from app.modules.monitoring.ports.execution import MonitoringRunRepositoryPort


class ControlOutcomeKind(str, Enum):
    COMPLETED = "completed"
    RETRY_TRANSIENT = "retry_transient"
    RETRY_AT = "retry_at"
    OBLIGATION_SATISFIED = "obligation_satisfied"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True)
class ControlOutcome:
    kind: ControlOutcomeKind
    payload: dict[str, object]
    retry_after: int | None = None


class CompletionEvaluator(Protocol):
    async def execute_event(self, payload: Mapping[str, object]) -> dict[str, object]: ...


class ProcessMonitoringControlEvent:
    def __init__(
        self,
        evaluator: CompletionEvaluator,
        trackers: RunCompletionTrackerRepositoryPort,
        runs: MonitoringRunRepositoryPort,
    ) -> None:
        self._evaluator = evaluator
        self._trackers = trackers
        self._runs = runs

    async def execute(
        self, payload: Mapping[str, object], outbox_id: UUID | None = None,
    ) -> ControlOutcome:
        run_id = self._run_id(payload)
        if run_id is None:
            if outbox_id is not None:
                await self._trackers.reject_invalid_outbox(outbox_id, "InvalidRunId")
            return ControlOutcome(
                ControlOutcomeKind.PERMANENT_FAILURE,
                {"status": "invalid_event", "error_type": "InvalidRunId"},
            )

        if not self._valid_payload(payload):
            return await self._permanent_failure(run_id, "InvalidControlEvent", outbox_id)

        try:
            result = (
                await self._trackers.apply_outbox_event(payload, outbox_id, datetime.now(UTC))
                if outbox_id is not None
                else await self._evaluator.execute_event(payload)
            )
        except TransientDeadlineSchedulingError:
            if not await self._deadline_obligation_pending(run_id):
                return ControlOutcome(
                    ControlOutcomeKind.OBLIGATION_SATISFIED,
                    {"status": "obligation_satisfied", "run_id": str(run_id),
                     "event": str(payload.get("event"))},
                )
            return ControlOutcome(
                ControlOutcomeKind.RETRY_TRANSIENT,
                {"status": "retry_transient", "run_id": str(run_id)},
            )
        except UnsupportedDeadlineHorizonError:
            return await self._permanent_failure(run_id, "UnsupportedDeadlineHorizonError", outbox_id)

        if result.get("status") == "early_delivery":
            return ControlOutcome(
                ControlOutcomeKind.RETRY_AT,
                result,
                retry_after=max(1, int(result.get("retry_after", 5))),
            )
        if result.get("status") == "analysis_ready_rejected":
            return ControlOutcome(ControlOutcomeKind.PERMANENT_FAILURE, result)
        if result.get("status") in {"error", "ignored"}:
            return await self._permanent_failure(run_id, "InvalidControlEvent", outbox_id)
        return ControlOutcome(ControlOutcomeKind.COMPLETED, result)

    async def _deadline_obligation_pending(self, run_id: UUID) -> bool:
        tracker = await self._trackers.get_by_run_id(run_id)
        if tracker is None:
            return True
        return (
            tracker.deadline_at is not None
            and tracker.deadline_scheduled_at is None
            and not tracker.status.is_terminal_or_dispatched
        )

    async def _permanent_failure(
        self, run_id: UUID, code: str, outbox_id: UUID | None = None,
    ) -> ControlOutcome:
        if outbox_id is not None:
            result = await self._trackers.close_outbox_control_failure(run_id, outbox_id, code)
            return ControlOutcome(ControlOutcomeKind.PERMANENT_FAILURE, result)
        tracker = await self._trackers.mark_control_failed(run_id)
        if tracker is None:
            tracker = await self._trackers.get_by_run_id(run_id)
        if tracker is None:
            run = await self._runs.get_by_id(run_id)
            if run is None:
                return ControlOutcome(
                    ControlOutcomeKind.PERMANENT_FAILURE,
                    {"status": "invalid_event", "run_id": str(run_id), "error_type": code},
                )
            if run.status.value == "cancelled":
                return ControlOutcome(ControlOutcomeKind.OBLIGATION_SATISFIED,
                    {"status": "obligation_satisfied", "run_id": str(run_id), "error_type": code})
            await self._trackers.get_or_create(run_id)
            tracker = await self._trackers.mark_control_failed(run_id)
            if tracker is None:
                tracker = await self._trackers.get_by_run_id(run_id)
        if tracker is None:
            raise RuntimeError(f"Unable to close control failure for run {run_id}")
        return ControlOutcome(
            ControlOutcomeKind.PERMANENT_FAILURE,
            {"status": "control_failed" if tracker.status.value == "control_failed" else
             "obligation_satisfied", "run_id": str(run_id), "error_type": code},
        )

    @staticmethod
    def _run_id(payload: Mapping[str, object]) -> UUID | None:
        try:
            return UUID(str(payload.get("run_id", "")))
        except (TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _valid_payload(payload: Mapping[str, object]) -> bool:
        event = payload.get("event")
        required_uuid = {
            "source_terminal": "source_task_id",
            "analysis_terminal": "source_analysis_id",
            "analysis_ready": "aggregation_id",
        }
        if not isinstance(event, str) or event not in {
            *required_uuid, "deadline_reached", "deadline_check"
        }:
            return False
        if event in required_uuid:
            try:
                UUID(str(payload.get(required_uuid[event], "")))
            except (TypeError, ValueError, AttributeError):
                return False
        if event in {"source_terminal", "analysis_terminal"} and payload.get(
            "outcome", "completed"
        ) not in {"completed", "failed", "cancelled"}:
            return False
        for field in ("occurred_at", "deadline_at"):
            if payload.get(field) is not None:
                try:
                    datetime.fromisoformat(str(payload[field]))
                except (TypeError, ValueError):
                    return False
        return True
