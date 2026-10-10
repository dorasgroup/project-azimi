"""Batched read-only projection from durable distributed lifecycle counters."""

from sqlalchemy import func, select
from app.models.monitoring_run import MonitoringRun
from app.models.source_task import SourceTask
from app.models.completion_tracker import OrmRunCompletionTracker
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.monitoring.domain.progress import MonitoringProgressSnapshot


class DistributedProgressReader:
    def __init__(self, session) -> None:
        self._session = session

    async def read_many(self, job_ids):
        if not job_ids:
            return {}
        latest = select(
            MonitoringRun.id.label("run_id"),
            func.row_number().over(
                partition_by=MonitoringRun.monitoring_job_id,
                order_by=(MonitoringRun.created_at.desc(), MonitoringRun.id.desc()),
            ).label("rank"),
        ).where(MonitoringRun.monitoring_job_id.in_(job_ids)).subquery()
        rows = (await self._session.execute(select(
            MonitoringRun, OrmRunCompletionTracker,
            func.coalesce(func.sum(SourceTask.documents_count), 0).label("documents"),
        ).join(latest, (latest.c.run_id == MonitoringRun.id) & (latest.c.rank == 1))
          .outerjoin(OrmRunCompletionTracker, OrmRunCompletionTracker.run_id == MonitoringRun.id)
          .outerjoin(SourceTask, SourceTask.run_id == MonitoringRun.id)
          .where(MonitoringRun.monitoring_job_id.in_(job_ids), MonitoringRun.execution_mode == "distributed",
                 MonitoringRun.status.in_([MonitoringRunStatus.PENDING, MonitoringRunStatus.RUNNING,
                                          MonitoringRunStatus.CANCELLED]))
          .group_by(MonitoringRun.id, OrmRunCompletionTracker.id))).all()
        result = {}
        for run, tracker, documents in rows:
            total = max(0, run.expected_sources)
            done = min(total, max(0, run.terminal_sources))
            if run.status == MonitoringRunStatus.CANCELLED:
                # Exact-run closure deliberately preserves historical Job rows.
                # Expose its durable disposition without mutating/replaying them.
                result[run.monitoring_job_id] = MonitoringProgressSnapshot(
                    "cancelled", 0, done, total, 0, int(documents), None,
                    "آخرین اجرای این رصد لغو شده است؛ اجرای فعالی ندارد.",
                )
                continue
            analyzed = tracker.analysis_terminal_count if tracker else 0
            crawl_fraction = done / total if total else 0
            analysis_fraction = min(done, analyzed) / total if total else 0
            percent = min(90, int(70 * crawl_fraction + 20 * analysis_fraction))
            stage = "distributed_crawling"
            message = (f"خزش {done} از {total} منبع پایان یافته؛ تحلیل {analyzed} منبع پایان یافته. "
                       f"خزش ناموفق: {run.failed_sources}. تعداد صفحات در حال خزش و زمان باقی‌مانده هنوز قابل محاسبه نیست.")
            if tracker and tracker.status == "aggregation_dispatched":
                stage, percent = "distributed_aggregating", 90
                message = "خزش و تحلیل منابع پایان یافته؛ جمع‌بندی نهایی در حال انجام است."
            elif tracker and tracker.status == "analysis_ready":
                stage, percent = "distributed_reporting", 95
                message = "جمع‌بندی معتبر آماده است؛ فایل‌های PDF و Word در حال تولید هستند."
            elif done == total and total:
                stage = "distributed_analyzing"
            result[run.monitoring_job_id] = MonitoringProgressSnapshot(
                stage, percent, done, total, 0, int(documents), None, message,
            )
        return result
