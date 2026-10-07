"""Atomic MonitoringRun finalization, terminal deduplication and consumer ACK."""

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
from app.shared.integration.report_terminal import ReportTerminalSnapshot


class MonitoringReportFinalizationRepository:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    async def finalize(self, report: ReportTerminalSnapshot, outbox_id: UUID | None, now: datetime) -> str:
        async with self._sessions() as session:
            run = await session.scalar(select(MonitoringRun).where(MonitoringRun.id == report.run_id).with_for_update())
            if run is None or run.execution_mode != "distributed":
                raise ValueError("Report finalization must target an existing distributed run")
            if run.status not in {MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING}:
                # Cancellation/control may have terminalized the run and tracker
                # while rendering finished. This delivery cannot change the run
                # and must not become an infinite retry against a closed tracker.
                await acknowledge_in_session(session, outbox_id)
                await session.commit()
                return "already_final"
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run.id))
            if tracker is None or tracker.status != "analysis_ready" or tracker.aggregation_id != report.aggregation_id:
                raise ValueError("Report finalization lacks matching ANALYSIS_READY predecessor")
            job = await session.scalar(select(MonitoringJob).where(MonitoringJob.id == run.monitoring_job_id).with_for_update())
            if job is None:
                raise ValueError("Report finalization references missing job")
            inserted = await session.execute(insert(OrmRunCompletionEvent).values(
                id=uuid4(), run_id=run.id, event_kind="report_terminal",
                entity_id=report.generation_id, outcome=report.status, occurred_at=now,
            ).on_conflict_do_nothing(constraint="uq_run_completion_events_kind_entity"))
            changed = False
            if inserted.rowcount == 1 and run.status in {MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING}:
                outcome = "cancelled" if job.status == JobStatus.CANCELLED else report.status
                if outcome not in {"completed", "failed", "cancelled"}:
                    raise ValueError("Invalid durable report terminal outcome")
                run.status = MonitoringRunStatus(outcome)
                run.finished_at = now
                run.updated_at = now
                job.status = JobStatus(outcome)
                job.finished_at = now
                job.updated_at = now
                if outcome == "failed":
                    job.error_message = "تولید فایل گزارش ناموفق شد."
                changed = True
            await acknowledge_in_session(session, outbox_id)
            await session.commit()
            return "finalized" if changed else "already_final"
