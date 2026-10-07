"""Historical rows stay untouched while the latest run drives live display."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import pytest
from sqlalchemy import text

from app.db.session import SessionFactory
from app.models import MonitoringJob, MonitoringRun
from app.api.operations.monitoring import _to_read
from app.modules.monitoring.infrastructure.distributed_progress import DistributedProgressReader
from app.modules.monitoring.infrastructure.repository import SqlAlchemyMonitoringJobRepository
from test_phase9_native_cutover import fixture_job


@pytest.mark.asyncio
async def test_cancelled_latest_read_does_not_mutate_historical_job():
    job_id, _ = await fixture_job()
    async with SessionFactory() as s, s.begin():
        job = await s.get(MonitoringJob, job_id)
        job.status = "crawling"
        s.add(MonitoringRun(id=uuid4(), monitoring_job_id=job_id, status="cancelled",
                           execution_mode="distributed", expected_sources=0))
    async with SessionFactory() as s:
        before = await s.scalar(text('SELECT md5(to_jsonb(j)::text) FROM monitoring_jobs j WHERE id=:id'), {'id':job_id})
        progress = (await DistributedProgressReader(s).read_many([job_id]))[job_id]
        record = await SqlAlchemyMonitoringJobRepository(s).get_by_id(job_id, include_sources=True)
        assert _to_read(record, progress).status == "cancelled"
        assert record.status.value == "crawling" and progress.stage == "cancelled"
        after = await s.scalar(text('SELECT md5(to_jsonb(j)::text) FROM monitoring_jobs j WHERE id=:id'), {'id':job_id})
        assert before == after


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['distributed','legacy'])
async def test_old_cancelled_run_cannot_override_newer_execution(mode):
    job_id, _ = await fixture_job()
    now = datetime.now(UTC)
    async with SessionFactory() as s, s.begin():
        s.add_all([
            MonitoringRun(id=uuid4(), monitoring_job_id=job_id, status="cancelled", execution_mode="distributed",
                          expected_sources=0, created_at=now-timedelta(seconds=1)),
            MonitoringRun(id=uuid4(), monitoring_job_id=job_id, status="running", execution_mode=mode,
                          expected_sources=0, created_at=now),
        ])
    async with SessionFactory() as s:
        result = await DistributedProgressReader(s).read_many([job_id])
        if mode == 'legacy':
            assert job_id not in result
        else:
            assert result[job_id].stage == 'distributed_crawling'


@pytest.mark.asyncio
async def test_completed_job_history_is_not_overridden_by_cancelled_run():
    job_id, _ = await fixture_job()
    async with SessionFactory() as s, s.begin():
        job = await s.get(MonitoringJob, job_id)
        job.status = 'completed'
        s.add(MonitoringRun(id=uuid4(), monitoring_job_id=job_id, status='cancelled', execution_mode='distributed', expected_sources=0))
    async with SessionFactory() as s:
        record = await SqlAlchemyMonitoringJobRepository(s).get_by_id(job_id, include_sources=True)
        progress = (await DistributedProgressReader(s).read_many([job_id]))[job_id]
        assert _to_read(record, progress).status == 'completed'
