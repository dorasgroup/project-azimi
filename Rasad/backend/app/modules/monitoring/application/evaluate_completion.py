"""Application use case for evaluating run completion and triggering fan-in aggregation."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from typing import Mapping
from uuid import UUID

from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTrackerStatus,
    evaluate_tracker_readiness,
)
from app.modules.monitoring.ports.completion import (
    DeadlineSchedulerPort,
    DeadlinePolicyPort,
    RunAggregationHandoffPort,
    RunCompletionTrackerRepositoryPort,
)
from app.modules.monitoring.ports.execution import MonitoringRunRepositoryPort
from app.modules.monitoring.application.create_completion_tracker import CreateRunCompletionTracker

logger = logging.getLogger("rasad.monitoring.completion")


class EvaluateRunCompletionUseCase:
    """Consumes terminal signals, updates durable completion tracker, and triggers aggregation."""

    def __init__(
        self,
        tracker_repository: RunCompletionTrackerRepositoryPort,
        run_repository: MonitoringRunRepositoryPort,
        aggregation_handoff: RunAggregationHandoffPort,
        deadline_scheduler: DeadlineSchedulerPort | None = None,
        deadline_policy: DeadlinePolicyPort | None = None,
    ) -> None:
        self._tracker_repo = tracker_repository
        self._run_repo = run_repository
        self._aggregation_handoff = aggregation_handoff
        self._deadline_scheduler = deadline_scheduler
        self._deadline_policy = deadline_policy
        self._tracker_creator = (
            CreateRunCompletionTracker(tracker_repository, deadline_policy)
            if deadline_policy is not None else None
        )

    async def execute_event(self, payload: Mapping[str, object]) -> dict[str, object]:
        event_type = str(payload.get("event", ""))
        run_id_str = str(payload.get("run_id", ""))
        if not run_id_str:
            return {"status": "error", "message": "Missing run_id"}

        run_id = UUID(run_id_str)
        now = datetime.now(UTC)

        if event_type == "source_terminal":
            task_id = UUID(str(payload.get("source_task_id")))
            outcome = str(payload.get("outcome", "completed"))
            occurred_at_raw = payload.get("occurred_at")
            occurred_at = (
                datetime.fromisoformat(str(occurred_at_raw))
                if occurred_at_raw
                else now
            )
            is_new, tracker = await self._tracker_repo.record_event_and_update_counts(
                run_id=run_id,
                event_kind="source_terminal",
                entity_id=task_id,
                outcome=outcome,
                occurred_at=occurred_at,
            )
            return await self._evaluate_and_dispatch(run_id, now)

        elif event_type == "analysis_terminal":
            analysis_id = UUID(str(payload.get("source_analysis_id")))
            outcome = str(payload.get("outcome", "completed"))
            occurred_at_raw = payload.get("occurred_at")
            occurred_at = (
                datetime.fromisoformat(str(occurred_at_raw))
                if occurred_at_raw
                else now
            )
            is_new, tracker = await self._tracker_repo.record_event_and_update_counts(
                run_id=run_id,
                event_kind="analysis_terminal",
                entity_id=analysis_id,
                outcome=outcome,
                occurred_at=occurred_at,
            )
            return await self._evaluate_and_dispatch(run_id, now)

        elif event_type in ("deadline_reached", "deadline_check"):
            tracker = await self._tracker_repo.get_by_run_id(run_id)
            if tracker is None:
                deadline_raw = payload.get("deadline_at")
                deadline_for_creation = (
                    datetime.fromisoformat(str(deadline_raw)) if deadline_raw else None
                )
                if self._tracker_creator is not None:
                    tracker = await self._tracker_creator.execute(run_id, deadline_for_creation)
                else:
                    tracker = await self._tracker_repo.get_or_create(
                        run_id, deadline_at=deadline_for_creation
                    )

            if tracker is None:
                return {"status": "obligation_satisfied", "run_id": str(run_id)}
            # Check if event arrived early (Section 8)
            deadline_at = tracker.deadline_at
            if deadline_at is None and payload.get("deadline_at"):
                deadline_at = datetime.fromisoformat(str(payload.get("deadline_at")))

            if deadline_at is not None and now < deadline_at:
                remaining_seconds = max(1, int((deadline_at - now).total_seconds()) + 1)
                return {
                    "status": "early_delivery",
                    "run_id": str(run_id),
                    "retry_after": remaining_seconds,
                    "deadline_at": deadline_at.isoformat(),
                }

            return await self._evaluate_and_dispatch(run_id, now)

        elif event_type == "analysis_ready":
            agg_id = UUID(str(payload.get("aggregation_id")))
            tracker = await self._tracker_repo.mark_analysis_ready(run_id, agg_id, now)
            if (
                tracker is None or tracker.status != RunCompletionTrackerStatus.ANALYSIS_READY
                or tracker.aggregation_id != agg_id
            ):
                return {
                    "status": "analysis_ready_rejected",
                    "run_id": str(run_id),
                    "aggregation_id": str(agg_id),
                    "tracker_status": tracker.status.value if tracker else "missing",
                }
            return {
                "status": "analysis_ready",
                "run_id": str(run_id),
                "aggregation_id": str(agg_id),
            }

        return {"status": "ignored", "event": event_type}

    async def _evaluate_and_dispatch(
        self,
        run_id: UUID,
        now: datetime,
    ) -> dict[str, object]:
        tracker = await self._tracker_repo.get_by_run_id(run_id)
        if tracker is None:
            tracker = await self._tracker_repo.get_or_create(run_id)

        if tracker is None:
            return {"status": "obligation_satisfied", "run_id": str(run_id)}

        # Section 7: Deadline dispatch recovery
        if (
            tracker.deadline_at is not None
            and getattr(tracker, "deadline_scheduled_at", None) is None
            and not tracker.status.is_terminal_or_dispatched
        ):
            if self._deadline_scheduler is not None:
                self._deadline_scheduler.schedule_deadline_signal(
                    run_id=run_id,
                    tracker_id=tracker.id,
                    deadline_at=tracker.deadline_at,
                )
                tracker = await self._tracker_repo.mark_deadline_scheduled(run_id, now)
                if tracker is None:
                    return {"status": "obligation_satisfied", "run_id": str(run_id)}

        # Recovery for crash after READY but before AGGREGATION_DISPATCHED
        if tracker.status in (
            RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            RunCompletionTrackerStatus.READY_DEADLINE,
        ):
            return {"status": "aggregation_queued", "run_id": str(run_id)}

        if tracker.status.is_terminal_or_dispatched:
            return {"status": tracker.status.value, "run_id": str(run_id)}

        run = await self._run_repo.get_by_id(run_id)
        if run is None:
            return {"status": "run_not_found", "run_id": str(run_id)}

        is_ready, reason = evaluate_tracker_readiness(
            expected_sources=run.expected_sources,
            terminal_sources=run.terminal_sources,
            successful_sources=run.successful_sources,
            tracker=tracker,
            now=now,
        )

        if not is_ready or reason is None:
            return {
                "status": "open",
                "run_id": str(run_id),
                "terminal_sources": run.terminal_sources,
                "analysis_terminal": tracker.analysis_terminal_count,
            }

        if reason == "deadline":
            new_status = RunCompletionTrackerStatus.READY_DEADLINE
            cutoff_at = tracker.deadline_at if tracker.deadline_at is not None else now
        else:
            new_status = RunCompletionTrackerStatus.READY_ALL_TERMINAL
            cutoff_at = now

        # Atomic transition: only one concurrent worker succeeds
        ready_tracker = await self._tracker_repo.transition_to_ready(
            run_id=run_id,
            expected_status=RunCompletionTrackerStatus.OPEN,
            new_status=new_status,
            readiness_reason=reason,
            cutoff_at=cutoff_at,
            now=now,
        )

        if ready_tracker is None:
            # Another concurrent worker already transitioned
            updated = await self._tracker_repo.get_by_run_id(run_id)
            if updated and updated.status in (
                RunCompletionTrackerStatus.READY_ALL_TERMINAL,
                RunCompletionTrackerStatus.READY_DEADLINE,
            ):
                return {"status": "aggregation_queued", "run_id": str(run_id)}
            return {"status": "contention_handled", "run_id": str(run_id)}

        return {"status": "aggregation_queued", "run_id": str(run_id)}

    async def _dispatch_aggregation(
        self,
        run_id: UUID,
        tracker: object,
        reason: str,
        now: datetime,
    ) -> dict[str, object]:
        run = await self._run_repo.get_by_id(run_id)
        if run is None:
            return {"status": "run_not_found", "run_id": str(run_id)}

        metadata = {
            "expected_sources": run.expected_sources,
            "successful_sources": run.successful_sources,
            "failed_sources": run.failed_sources,
            "cancelled_sources": run.cancelled_sources,
            "analysis_successful": getattr(tracker, "analysis_successful_count", 0),
            "analysis_failed": getattr(tracker, "analysis_failed_count", 0),
            "analysis_cancelled": getattr(tracker, "analysis_cancelled_count", 0),
        }

        cutoff_at = getattr(tracker, "cutoff_at", now)
        agg_id = await self._aggregation_handoff.ensure_and_dispatch_aggregation(
            run_id=run.id,
            readiness_reason=reason,
            cutoff_at=cutoff_at,
            completion_metadata=metadata,
        )

        if agg_id is None:
            return {"status": "obligation_satisfied", "run_id": str(run_id)}

        await self._tracker_repo.mark_aggregation_dispatched(run.id, agg_id)

        return {
            "status": "aggregation_dispatched",
            "run_id": str(run.id),
            "aggregation_id": str(agg_id),
            "readiness_reason": reason,
            "cutoff_at": cutoff_at.isoformat() if cutoff_at else None,
        }
