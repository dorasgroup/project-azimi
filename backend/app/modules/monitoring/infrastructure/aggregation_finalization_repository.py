"""Atomic failure deduplication, Monitoring terminal transition and outbox ACK."""

from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from app.models.completion_tracker import OrmRunCompletionEvent, OrmRunCompletionTracker
from app.models.enums import JobStatus
from app.models.monitoring_job import MonitoringJob
from app.models.monitoring_run import MonitoringRun
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.integration.aggregation_terminal import AggregationTerminalSnapshot


class MonitoringAggregationFinalizationRepository:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    async def finalize(self, outcome: AggregationTerminalSnapshot, outbox_id: UUID | None, now: datetime) -> str:
        async with self._sessions() as session:
            run = await session.scalar(select(MonitoringRun).where(MonitoringRun.id == outcome.run_id).with_for_update())
            if run is None or run.execution_mode != "distributed":
                raise ValueError("Aggregation finalization requires existing distributed run")
            if run.status not in {MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING}:
                await acknowledge_in_session(session, outbox_id)
                await session.commit()
                return "already_final"
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run.id).with_for_update())
            if tracker is None or tracker.status != "aggregation_dispatched" or tracker.aggregation_id != outcome.aggregation_id:
                raise ValueError("Aggregation terminal lacks matching predecessor")
            job = await session.scalar(select(MonitoringJob).where(MonitoringJob.id == run.monitoring_job_id).with_for_update())
            if job is None:
                raise ValueError("Aggregation terminal references missing job")
            inserted = await session.execute(insert(OrmRunCompletionEvent).values(
                id=uuid4(), run_id=run.id, event_kind="aggregation_terminal",
                entity_id=outcome.aggregation_id, outcome="failed", occurred_at=now,
            ).on_conflict_do_nothing(constraint="uq_run_completion_events_kind_entity"))
            if inserted.rowcount != 1:
                raise ValueError("Aggregation dedup conflicts with active run")
            cancelled = job.status == JobStatus.CANCELLED
            run.status = MonitoringRunStatus.CANCELLED if cancelled else MonitoringRunStatus.FAILED
            run.finished_at = run.updated_at = now
            job.status = JobStatus.CANCELLED if cancelled else JobStatus.FAILED
            job.finished_at = job.updated_at = now
            if not cancelled:
                job.error_message = (
                    "تحلیل اسناد منابع به دلیل خطای سرویس تحلیل ناموفق شد؛ "
                    "این خطا به معنی نبود خبر مرتبط نیست. گزارش بدون تحلیل معتبر تولید نشده است."
                    if outcome.reason_code == "SOURCE_ANALYSIS_FAILED" else (
                    "در منابع انتخاب‌شده و بازه زمانی این رصد، شواهد مرتبط کافی برای تهیه گزارش به دست نیامد. "
                    "منابع و بازه زمانی را بررسی کنید؛ گزارشی بدون شواهد تولید نشده است."
                    if outcome.reason_code == "NO_RELEVANT_EVIDENCE"
                    else "جمع‌بندی تحلیل منابع ناموفق شد؛ گزارش معتبری تولید نشده است."
                ))
            tracker.status = "cancelled" if cancelled else "control_failed"
            tracker.updated_at = now
            await acknowledge_in_session(session, outbox_id)
            await session.commit()
            return "finalized"

