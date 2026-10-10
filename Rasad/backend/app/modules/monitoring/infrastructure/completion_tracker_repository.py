"""SQLAlchemy implementation of RunCompletionTrackerRepositoryPort."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.shared.infrastructure.run_fence import lock_run

from app.models.completion_tracker import (
    OrmRunCompletionEvent,
    OrmRunCompletionTracker,
)
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.models.integration_outbox import IntegrationOutbox
from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTracker,
    RunCompletionTrackerStatus,
    evaluate_tracker_readiness,
)
from app.modules.monitoring.ports.completion import (
    RunCompletionTrackerRepositoryPort,
)
from app.shared.integration.outbox import enqueue
from app.shared.integration.consumer_ack import acknowledge_in_session


def _to_domain(orm: OrmRunCompletionTracker) -> RunCompletionTracker:
    return RunCompletionTracker(
        id=orm.id,
        run_id=orm.run_id,
        status=RunCompletionTrackerStatus(orm.status),
        analysis_terminal_count=orm.analysis_terminal_count,
        analysis_successful_count=orm.analysis_successful_count,
        analysis_failed_count=orm.analysis_failed_count,
        analysis_cancelled_count=orm.analysis_cancelled_count,
        deadline_at=orm.deadline_at,
        ready_at=orm.ready_at,
        cutoff_at=orm.cutoff_at,
        readiness_reason=orm.readiness_reason,
        aggregation_id=orm.aggregation_id,
        analysis_ready_at=orm.analysis_ready_at,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        deadline_scheduled_at=getattr(orm, "deadline_scheduled_at", None),
    )


class SqlAlchemyRunCompletionTrackerRepository(RunCompletionTrackerRepositoryPort):
    """Repository managing durable completion trackers and deduplication events."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _existing(self, session: AsyncSession, run_id: UUID) -> RunCompletionTracker | None:
        orm = await session.scalar(select(OrmRunCompletionTracker)
            .where(OrmRunCompletionTracker.run_id == run_id))
        return _to_domain(orm) if orm else None

    async def get_or_create(
        self,
        run_id: UUID,
        deadline_at: datetime | None = None,
    ) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return await self._existing(session, run_id)
            stmt = (
                pg_insert(OrmRunCompletionTracker)
                .values(
                    id=uuid4(),
                    run_id=run_id,
                    status=RunCompletionTrackerStatus.OPEN.value,
                    analysis_terminal_count=0,
                    analysis_successful_count=0,
                    analysis_failed_count=0,
                    analysis_cancelled_count=0,
                    deadline_at=deadline_at,
                )
                .on_conflict_do_nothing(index_elements=["run_id"])
            )
            await session.execute(stmt)
            res = await session.execute(
                select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id)
            )
            orm = res.scalar_one()
            if orm.deadline_at is None and deadline_at is not None:
                orm.deadline_at = deadline_at
            if orm.deadline_at is not None:
                await enqueue(
                    session,
                    message_key=f"deadline:{run_id}:{orm.deadline_at.isoformat()}",
                    message_type="DEADLINE_REACHED",
                    payload={
                        "schema_version": 1, "event": "deadline_reached",
                        "run_id": str(run_id), "tracker_id": str(orm.id),
                        "deadline_at": orm.deadline_at.isoformat(),
                    },
                    available_at=orm.deadline_at,
                )
                orm.deadline_scheduled_at = datetime.now(UTC)
            await session.flush()
            await session.refresh(orm)
            result = _to_domain(orm)
            await session.commit()
            return result

    async def get_by_run_id(self, run_id: UUID) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            res = await session.execute(
                select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id)
            )
            orm = res.scalar_one_or_none()
            return _to_domain(orm) if orm else None

    async def apply_outbox_event(
        self, payload: Mapping[str, object], outbox_id: UUID, now: datetime,
    ) -> dict[str, object]:
        """Close one control delivery in the same commit as its durable effects.

        This path is used only for relay-originated messages. Legacy control
        deliveries retain the existing application-level repository calls.
        """
        run_id = UUID(str(payload["run_id"]))
        event = str(payload["event"])
        async with self._session_factory() as session:
            parent = await lock_run(session, run_id)
            if parent == "cancelled":
                await acknowledge_in_session(session, outbox_id)
                await session.commit()
                return {"status": "obligation_satisfied", "run_id": str(run_id)}
            run = await session.get(OrmMonitoringRun, run_id)
            if run is None:
                raise ValueError(f"Control outbox references missing run {run_id}")

            tracker = await session.scalar(
                select(OrmRunCompletionTracker)
                .where(OrmRunCompletionTracker.run_id == run_id)
                .with_for_update()
            )
            if tracker is None:
                if event == "analysis_ready":
                    raise ValueError(f"Analysis-ready outbox references missing tracker {run_id}")
                deadline_raw = payload.get("deadline_at")
                deadline_at = datetime.fromisoformat(str(deadline_raw)) if deadline_raw else None
                await session.execute(
                    pg_insert(OrmRunCompletionTracker)
                    .values(
                        id=uuid4(), run_id=run_id,
                        status=RunCompletionTrackerStatus.OPEN.value,
                        analysis_terminal_count=0, analysis_successful_count=0,
                        analysis_failed_count=0, analysis_cancelled_count=0,
                        deadline_at=deadline_at,
                    )
                    .on_conflict_do_nothing(index_elements=["run_id"])
                )
                tracker = await session.scalar(
                    select(OrmRunCompletionTracker)
                    .where(OrmRunCompletionTracker.run_id == run_id)
                    .with_for_update()
                )
                if tracker is None:
                    raise RuntimeError(f"Unable to create control tracker for {run_id}")

            if event == "analysis_ready":
                if run.status != MonitoringRunStatus.RUNNING:
                    await acknowledge_in_session(session, outbox_id)
                    await session.commit()
                    return {"status": "obligation_satisfied", "run_id": str(run_id)}
                aggregation_id = UUID(str(payload["aggregation_id"]))
                result_id_raw = payload.get("result_id")
                if result_id_raw is None:
                    return {"status": "analysis_ready_rejected", "run_id": str(run_id), "reason": "missing_result_id"}
                result_id = UUID(str(result_id_raw))
                if (
                    tracker.status == RunCompletionTrackerStatus.AGGREGATION_DISPATCHED.value
                    and tracker.aggregation_id == aggregation_id
                ):
                    tracker.status = RunCompletionTrackerStatus.ANALYSIS_READY.value
                    tracker.analysis_ready_at = now
                    tracker.updated_at = now
                elif not (
                    tracker.status == RunCompletionTrackerStatus.ANALYSIS_READY.value
                    and tracker.aggregation_id == aggregation_id
                ):
                    # An impossible/out-of-order event remains unacknowledged for
                    # investigation instead of falsely closing the obligation.
                    return {
                        "status": "analysis_ready_rejected", "run_id": str(run_id),
                        "aggregation_id": str(aggregation_id),
                        "tracker_status": tracker.status,
                    }
                result: dict[str, object] = {
                    "status": "analysis_ready", "run_id": str(run_id),
                    "aggregation_id": str(aggregation_id),
                }
                if result_id is not None:
                    await enqueue(
                        session,
                        message_key=f"report-generation-handoff:{run_id}:{result_id}",
                        message_type="REPORT_GENERATION_HANDOFF",
                        payload={
                            "schema_version": 1, "run_id": str(run_id),
                            "result_id": str(result_id),
                        },
                    )
            else:
                if event in ("source_terminal", "analysis_terminal"):
                    entity_id = UUID(str(payload[
                        "source_task_id" if event == "source_terminal" else "source_analysis_id"
                    ]))
                    outcome = str(payload.get("outcome", "completed"))
                    occurred_raw = payload.get("occurred_at")
                    occurred_at = datetime.fromisoformat(str(occurred_raw)) if occurred_raw else now
                    inserted = await session.execute(
                        pg_insert(OrmRunCompletionEvent)
                        .values(
                            id=uuid4(), run_id=run_id, event_kind=event,
                            entity_id=entity_id, outcome=outcome, occurred_at=occurred_at,
                        )
                        .on_conflict_do_nothing(constraint="uq_run_completion_events_kind_entity")
                    )
                    if (inserted.rowcount or 0) > 0 and event == "analysis_terminal":
                        tracker.analysis_terminal_count += 1
                        if outcome == "completed":
                            tracker.analysis_successful_count += 1
                        elif outcome == "failed":
                            tracker.analysis_failed_count += 1
                        elif outcome == "cancelled":
                            tracker.analysis_cancelled_count += 1
                        tracker.updated_at = now
                elif event in ("deadline_reached", "deadline_check"):
                    deadline_raw = payload.get("deadline_at")
                    if tracker.deadline_at is None and deadline_raw:
                        tracker.deadline_at = datetime.fromisoformat(str(deadline_raw))
                    if tracker.deadline_at is not None and now < tracker.deadline_at:
                        retry_after = max(1, int((tracker.deadline_at - now).total_seconds()) + 1)
                        return {
                            "status": "early_delivery", "run_id": str(run_id),
                            "retry_after": retry_after,
                            "deadline_at": tracker.deadline_at.isoformat(),
                        }
                else:
                    raise ValueError(f"Unsupported control outbox event {event}")

                current_status = RunCompletionTrackerStatus(tracker.status)
                if current_status.is_ready:
                    result = {"status": "aggregation_queued", "run_id": str(run_id)}
                elif current_status.is_terminal_or_dispatched:
                    result = {"status": current_status.value, "run_id": str(run_id)}
                else:
                    ready, reason = evaluate_tracker_readiness(
                        expected_sources=run.expected_sources,
                        terminal_sources=run.terminal_sources,
                        successful_sources=run.successful_sources,
                        tracker=_to_domain(tracker), now=now,
                    )
                    if ready and reason is not None:
                        tracker.status = (
                            RunCompletionTrackerStatus.READY_DEADLINE.value
                            if reason == "deadline" else RunCompletionTrackerStatus.READY_ALL_TERMINAL.value
                        )
                        tracker.readiness_reason = reason
                        tracker.ready_at = now
                        tracker.cutoff_at = tracker.deadline_at if reason == "deadline" else now
                        tracker.updated_at = now
                        await enqueue(
                            session,
                            message_key=f"run-aggregation-handoff:{run_id}",
                            message_type="RUN_AGGREGATION_HANDOFF",
                            payload={"schema_version": 1, "run_id": str(run_id)},
                        )
                        result = {"status": "aggregation_queued", "run_id": str(run_id)}
                    else:
                        result = {
                            "status": "open", "run_id": str(run_id),
                            "terminal_sources": run.terminal_sources,
                            "analysis_terminal": tracker.analysis_terminal_count,
                        }

            await acknowledge_in_session(session, outbox_id)
            await session.commit()
            return result

    async def close_outbox_control_failure(
        self, run_id: UUID, outbox_id: UUID, error_code: str,
    ) -> dict[str, object]:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) == "cancelled":
                await acknowledge_in_session(session, outbox_id)
                await session.commit()
                return {"status": "obligation_satisfied", "run_id": str(run_id), "error_type": error_code}
            run = await session.get(OrmMonitoringRun, run_id)
            tracker = await session.scalar(
                select(OrmRunCompletionTracker)
                .where(OrmRunCompletionTracker.run_id == run_id)
                .with_for_update()
            )
            if tracker is None and run is not None:
                await session.execute(
                    pg_insert(OrmRunCompletionTracker)
                    .values(
                        id=uuid4(), run_id=run_id,
                        status=RunCompletionTrackerStatus.OPEN.value,
                        analysis_terminal_count=0, analysis_successful_count=0,
                        analysis_failed_count=0, analysis_cancelled_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["run_id"])
                )
                tracker = await session.scalar(
                    select(OrmRunCompletionTracker)
                    .where(OrmRunCompletionTracker.run_id == run_id)
                    .with_for_update()
                )
            status = "obligation_satisfied"
            if tracker is not None and tracker.status == RunCompletionTrackerStatus.OPEN.value:
                tracker.status = RunCompletionTrackerStatus.CONTROL_FAILED.value
                tracker.updated_at = datetime.now(UTC)
                status = "control_failed"
            elif tracker is None:
                status = "invalid_event"
            await acknowledge_in_session(session, outbox_id)
            await session.commit()
            return {"status": status, "run_id": str(run_id), "error_type": error_code}

    async def reject_invalid_outbox(self, outbox_id: UUID, error_code: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(IntegrationOutbox)
                .where(IntegrationOutbox.id == outbox_id)
                .values(
                    status="FAILED", last_error_code=error_code[:100],
                    last_error_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                )
            )
            if result.rowcount != 1:
                raise ValueError(f"Unknown invalid control outbox {outbox_id}")
            await session.commit()

    async def record_event_and_update_counts(
        self,
        run_id: UUID,
        event_kind: str,
        entity_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> tuple[bool, RunCompletionTracker | None]:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return False, await self._existing(session, run_id)
            # 1. Deduplicate in run_completion_events
            stmt = (
                pg_insert(OrmRunCompletionEvent)
                .values(
                    id=uuid4(),
                    run_id=run_id,
                    event_kind=event_kind,
                    entity_id=entity_id,
                    outcome=outcome,
                    occurred_at=occurred_at,
                )
                .on_conflict_do_nothing(constraint="uq_run_completion_events_kind_entity")
            )
            res = await session.execute(stmt)
            is_new = (res.rowcount or 0) > 0

            # 2. Ensure tracker exists
            tracker_res = await session.execute(
                select(OrmRunCompletionTracker)
                .where(OrmRunCompletionTracker.run_id == run_id)
                .with_for_update()
            )
            orm = tracker_res.scalar_one_or_none()
            if orm is None:
                tracker_stmt = (
                    pg_insert(OrmRunCompletionTracker)
                    .values(
                        id=uuid4(),
                        run_id=run_id,
                        status=RunCompletionTrackerStatus.OPEN.value,
                        analysis_terminal_count=0,
                        analysis_successful_count=0,
                        analysis_failed_count=0,
                        analysis_cancelled_count=0,
                    )
                    .on_conflict_do_nothing(index_elements=["run_id"])
                )
                await session.execute(tracker_stmt)
                tracker_res = await session.execute(
                    select(OrmRunCompletionTracker)
                    .where(OrmRunCompletionTracker.run_id == run_id)
                    .with_for_update()
                )
                orm = tracker_res.scalar_one()

            # 3. If new analysis terminal event, increment counters
            if is_new and event_kind == "analysis_terminal":
                orm.analysis_terminal_count += 1
                if outcome == "completed":
                    orm.analysis_successful_count += 1
                elif outcome == "failed":
                    orm.analysis_failed_count += 1
                elif outcome == "cancelled":
                    orm.analysis_cancelled_count += 1
                orm.updated_at = datetime.now(UTC)

            await session.commit()
            return is_new, _to_domain(orm)

    async def transition_to_ready(
        self,
        run_id: UUID,
        expected_status: RunCompletionTrackerStatus,
        new_status: RunCompletionTrackerStatus,
        readiness_reason: str,
        cutoff_at: datetime,
        now: datetime,
    ) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return None
            stmt = (
                update(OrmRunCompletionTracker)
                .where(
                    OrmRunCompletionTracker.run_id == run_id,
                    OrmRunCompletionTracker.status == expected_status.value,
                )
                .values(
                    status=new_status.value,
                    readiness_reason=readiness_reason,
                    ready_at=now,
                    cutoff_at=cutoff_at,
                    updated_at=now,
                )
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one_or_none()
            if orm is None:
                await session.rollback()
                return None
            await enqueue(
                session,
                message_key=f"run-aggregation-handoff:{run_id}",
                message_type="RUN_AGGREGATION_HANDOFF",
                payload={"schema_version": 1, "run_id": str(run_id)},
            )
            await session.commit()
            return _to_domain(orm)

    async def mark_aggregation_dispatched(
        self,
        run_id: UUID,
        aggregation_id: UUID,
    ) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return await self._existing(session, run_id)
            now = datetime.now(UTC)
            stmt = (
                update(OrmRunCompletionTracker)
                .where(
                    OrmRunCompletionTracker.run_id == run_id,
                    OrmRunCompletionTracker.status != RunCompletionTrackerStatus.CONTROL_FAILED.value,
                )
                .values(
                    status=RunCompletionTrackerStatus.AGGREGATION_DISPATCHED.value,
                    aggregation_id=aggregation_id,
                    updated_at=now,
                )
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one()
            await session.commit()
            return _to_domain(orm)

    async def mark_analysis_ready(
        self,
        run_id: UUID,
        aggregation_id: UUID,
        ready_at: datetime,
    ) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return await self._existing(session, run_id)
            now = datetime.now(UTC)
            stmt = (
                update(OrmRunCompletionTracker)
                .where(
                    OrmRunCompletionTracker.run_id == run_id,
                    OrmRunCompletionTracker.status == RunCompletionTrackerStatus.AGGREGATION_DISPATCHED.value,
                    OrmRunCompletionTracker.aggregation_id == aggregation_id,
                )
                .values(
                    status=RunCompletionTrackerStatus.ANALYSIS_READY.value,
                    aggregation_id=aggregation_id,
                    analysis_ready_at=ready_at,
                    updated_at=now,
                )
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one_or_none()
            if orm is None:
                orm = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
                if orm is None:
                    raise ValueError("Completion tracker not found")
            await session.commit()
            return _to_domain(orm)

    async def mark_deadline_scheduled(
        self,
        run_id: UUID,
        scheduled_at: datetime,
    ) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return await self._existing(session, run_id)
            stmt = (
                update(OrmRunCompletionTracker)
                .where(
                    OrmRunCompletionTracker.run_id == run_id,
                    OrmRunCompletionTracker.status == RunCompletionTrackerStatus.OPEN.value,
                )
                .values(
                    deadline_scheduled_at=scheduled_at,
                    updated_at=datetime.now(UTC),
                )
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one()
            await session.commit()
            return _to_domain(orm)

    async def mark_cancelled(self, run_id: UUID) -> RunCompletionTracker:
        async with self._session_factory() as session:
            now = datetime.now(UTC)
            stmt = (
                update(OrmRunCompletionTracker)
                .where(OrmRunCompletionTracker.run_id == run_id)
                .values(
                    status=RunCompletionTrackerStatus.CANCELLED.value,
                    updated_at=now,
                )
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one()
            await session.commit()
            return _to_domain(orm)

    async def mark_control_failed(self, run_id: UUID) -> RunCompletionTracker | None:
        async with self._session_factory() as session:
            if await lock_run(session, run_id) in (None, "cancelled"):
                return await self._existing(session, run_id)
            now = datetime.now(UTC)
            stmt = (
                update(OrmRunCompletionTracker)
                .where(
                    OrmRunCompletionTracker.run_id == run_id,
                    OrmRunCompletionTracker.status == RunCompletionTrackerStatus.OPEN.value,
                )
                .values(status=RunCompletionTrackerStatus.CONTROL_FAILED.value, updated_at=now)
                .returning(OrmRunCompletionTracker)
            )
            res = await session.execute(stmt)
            orm = res.scalar_one_or_none()
            await session.commit()
            return _to_domain(orm) if orm else None
