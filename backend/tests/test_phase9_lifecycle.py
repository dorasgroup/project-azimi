"""Real isolated PostgreSQL failure/cancellation/projection acceptance."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from app.db.session import SessionFactory, engine
from app.models import MonitoringJob, MonitoringRun, IntegrationOutbox
from app.models.completion_tracker import OrmRunCompletionTracker, OrmRunCompletionEvent
from app.models.run_aggregation import OrmRunAggregation, OrmAggregatedAnalysisResult
from app.models.source_task import SourceTask
from app.modules.analysis.domain.source_analysis import PermanentAnalysisError, TransientAnalysisError
from app.modules.analysis.application.aggregate_analysis_use_case import ExecuteRunAggregationUseCase
from app.modules.analysis.infrastructure.run_aggregation_repository import SqlAlchemyRunAggregationRepository
from app.modules.analysis.infrastructure.run_analysis_spec_repository import SqlAlchemyRunAnalysisSpecRepository
from app.modules.analysis.infrastructure.aggregation_terminal_reader import AggregationTerminalReader
from app.modules.monitoring.application.finalize_aggregation import FinalizeMonitoringAggregation
from app.modules.monitoring.infrastructure.aggregation_finalization_repository import MonitoringAggregationFinalizationRepository
from app.modules.monitoring.infrastructure.distributed_progress import DistributedProgressReader
from app.modules.monitoring.application.use_cases import CancelMonitoringJob
from app.modules.monitoring.infrastructure.repository import SqlAlchemyMonitoringJobRepository
from app.shared.domain.clock import SystemClock
from app.bootstrap.run_cancellation import CancelJobRunsAdapter
from test_phase9_native_cutover import fixture_job


@pytest.mark.asyncio
async def test_no_evidence_reason_durably_reaches_job_without_fake_report():
    from app.modules.analysis.domain.source_analysis import NoRelevantEvidenceError
    job_id, run_id, aid = await fixture_aggregation()
    output = await executor(AsyncMock(aggregate=AsyncMock(side_effect=NoRelevantEvidenceError("private-marker")))).execute(aid)
    assert output['status'] == 'failed'
    reader = AggregationTerminalReader(SessionFactory)
    assert (await reader.read(aid)).reason_code == 'NO_RELEVANT_EVIDENCE'
    async with SessionFactory() as s:
        row = await s.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f'aggregation-terminal:{aid}'))
        oid = row.id
        agg = await s.get(OrmRunAggregation, aid)
        assert agg.error_code == 'NO_RELEVANT_EVIDENCE' and agg.attempt_count == 1
        assert 'private-marker' not in agg.error_message
    finalizer = FinalizeMonitoringAggregation(reader, MonitoringAggregationFinalizationRepository(SessionFactory))
    assert (await finalizer.execute(aid, oid))['status'] == 'finalized'
    assert (await finalizer.execute(aid, oid))['status'] == 'already_final'
    async with SessionFactory() as s:
        job = await s.get(MonitoringJob, job_id)
        assert 'شواهد مرتبط کافی' in job.error_message
        assert job.status.value == (await s.get(MonitoringRun, run_id)).status.value == 'failed'
        assert await s.scalar(select(func.count()).select_from(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id==aid)) == 0
        assert (await s.get(IntegrationOutbox, oid)).consumer_acked_at is not None


async def fixture_aggregation():
    job_id, _ = await fixture_job()
    run_id, aggregation_id = uuid4(), uuid4()
    async with SessionFactory() as s, s.begin():
        job = await s.get(MonitoringJob, job_id)
        job.status = "analyzing"
        s.add(MonitoringRun(id=run_id, monitoring_job_id=job_id, status="running",
                           execution_mode="distributed", expected_sources=1, terminal_sources=1, successful_sources=1))
        await s.flush()
        s.add(OrmRunAggregation(id=aggregation_id, run_id=run_id, status="pending", readiness_reason="all_terminal",
                               manifest_digest="a"*64, run_analysis_spec_digest="b"*64))
        s.add(OrmRunCompletionTracker(run_id=run_id, status="aggregation_dispatched", aggregation_id=aggregation_id,
                                     analysis_terminal_count=1, analysis_successful_count=1))
    return job_id, run_id, aggregation_id


def executor(aggregator):
    return ExecuteRunAggregationUseCase(SqlAlchemyRunAggregationRepository(SessionFactory),
        SqlAlchemyRunAnalysisSpecRepository(SessionFactory), aggregator, None)


@pytest.mark.asyncio
async def test_permanent_failure_durable_delivery_finalizes_both_run_and_job_once():
    job_id, run_id, aid = await fixture_aggregation()
    output = await executor(AsyncMock(aggregate=AsyncMock(side_effect=PermanentAnalysisError("secret-marker")))).execute(aid)
    assert output["status"] == "failed"
    async with SessionFactory() as s:
        agg = await s.get(OrmRunAggregation, aid)
        assert agg.status == "failed" and agg.claim_token is None and agg.lease_expires_at is None
        assert "secret-marker" not in agg.error_message
        row = await s.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f"aggregation-terminal:{aid}"))
        assert row.payload_json == {"schema_version":1, "aggregation_id":str(aid)}
        oid = row.id
        assert await s.scalar(select(func.count()).select_from(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id==aid)) == 0
    # Crash after failure/outbox commit and before transport ACK safely redelivers.
    assert (await executor(AsyncMock()).execute(aid))["status"] == "already_terminal"
    finalizer = FinalizeMonitoringAggregation(AggregationTerminalReader(SessionFactory), MonitoringAggregationFinalizationRepository(SessionFactory))
    assert (await finalizer.execute(aid, oid))["status"] == "finalized"
    assert (await finalizer.execute(aid, oid))["status"] == "already_final"
    async with SessionFactory() as s:
        assert (await s.get(MonitoringRun, run_id)).status.value == "failed"
        assert (await s.get(MonitoringJob, job_id)).status.value == "failed"
        assert (await s.get(IntegrationOutbox, oid)).consumer_acked_at is not None
        assert await s.scalar(select(func.count()).select_from(OrmRunCompletionEvent).where(OrmRunCompletionEvent.entity_id==aid)) == 1


@pytest.mark.asyncio
async def test_transient_retry_counter_monotonic_and_exhaustion_durable():
    _, _, aid = await fixture_aggregation()
    use_case = executor(AsyncMock(aggregate=AsyncMock(side_effect=TransientAnalysisError("private-body"))))
    for attempt in (1, 2, 3):
        out = await use_case.execute(aid)
        assert out["status"] == ("retry_transient" if attempt < 3 else "failed")
        async with SessionFactory() as s:
            row = await s.get(OrmRunAggregation, aid)
            assert row.attempt_count == attempt and row.claim_token is None and row.lease_expires_at is None


@pytest.mark.asyncio
async def test_failure_and_terminal_intent_roll_back_together():
    _, _, aid = await fixture_aggregation()
    repo = SqlAlchemyRunAggregationRepository(SessionFactory)
    token = uuid4()
    await repo.claim_explicit(aid, token, 60, datetime.now(UTC))
    with patch("app.modules.analysis.infrastructure.run_aggregation_repository.enqueue", new=AsyncMock(side_effect=RuntimeError("db"))):
        with pytest.raises(RuntimeError):
            await repo.mark_failed(aid, token, "TEST", "sanitized", datetime.now(UTC))
    async with SessionFactory() as s:
        assert (await s.get(OrmRunAggregation, aid)).status == "running"


@pytest.mark.asyncio
async def test_public_cancel_owner_only_and_atomic_child_closure():
    job_id, run_id, aid = await fixture_aggregation()
    async with SessionFactory() as s:
        owner = (await s.get(MonitoringJob, job_id)).requested_by
        uc = CancelMonitoringJob(SqlAlchemyMonitoringJobRepository(s), SystemClock(), CancelJobRunsAdapter(s))
        assert not await uc.execute(job_id, requester_id=uuid4(), role="user")
        await s.rollback()
        assert await uc.execute(job_id, requester_id=owner, role="user")
        await s.commit()
    async with SessionFactory() as s:
        assert (await s.get(MonitoringJob, job_id)).status.value == "cancelled"
        assert (await s.get(MonitoringRun, run_id)).status.value == "cancelled"
        assert (await s.get(OrmRunAggregation, aid)).status == "cancelled"
        assert (await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))).status == "cancelled"


@pytest.mark.asyncio
async def test_progress_reads_durable_run_stage_and_counts_without_eta():
    job_id, run_id, _ = await fixture_aggregation()
    async with SessionFactory() as s:
        p = (await DistributedProgressReader(s).read_many([job_id]))[job_id]
        assert p.sources_completed == p.sources_total == 1
        assert p.stage == "distributed_aggregating" and p.percent == 90
        assert p.eta_seconds is None and p.pages_processed == 0


@pytest.mark.asyncio
async def test_public_cancel_closes_report_claim_and_preserves_completed_input():
    from test_phase8_generation_repository import seed_ready, sessions
    from app.models.report_generation import OrmReportGeneration
    from app.modules.reports.infrastructure.generation_repository import SqlAlchemyReportGenerationRepository
    from app.modules.reports.infrastructure.terminal_reader import ReportTerminalReader
    from app.modules.monitoring.infrastructure.report_finalization_repository import MonitoringReportFinalizationRepository
    from app.modules.monitoring.application.finalize_report import FinalizeMonitoringReport
    run_id, result_id = await seed_ready()
    repo = SqlAlchemyReportGenerationRepository(sessions)
    gid = await repo.ensure_from_analysis_ready(run_id, result_id, None)
    token = uuid4()
    claim = await repo.claim(gid, token, 60, datetime.now(UTC))
    assert claim.kind.value == "claimed"
    async with sessions() as s:
        run = await s.get(MonitoringRun, run_id)
        job = await s.get(MonitoringJob, run.monitoring_job_id)
        job_id, owner = job.id, job.requested_by
        uc = CancelMonitoringJob(SqlAlchemyMonitoringJobRepository(s), SystemClock(), CancelJobRunsAdapter(s))
        assert await uc.execute(job_id, requester_id=owner, role="user")
        await s.commit()
    assert not await repo.renew_lease(gid, token, 60, datetime.now(UTC))
    async with sessions() as s:
        row = await s.get(OrmReportGeneration, gid)
        assert row.status == "cancelled" and row.claim_token is None and row.lease_expires_at is None
        assert await s.get(OrmAggregatedAnalysisResult, result_id) is not None
    result = await FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions)).execute(gid, None)
    assert result["status"] == "already_final"


@pytest.mark.asyncio
async def test_mismatched_aggregation_cannot_fail_unrelated_active_job():
    job_id, run_id, aid = await fixture_aggregation()
    await executor(AsyncMock(aggregate=AsyncMock(side_effect=PermanentAnalysisError("invalid")))).execute(aid)
    async with SessionFactory() as s, s.begin():
        tracker = await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
        tracker.aggregation_id = uuid4()
    finalizer = FinalizeMonitoringAggregation(AggregationTerminalReader(SessionFactory), MonitoringAggregationFinalizationRepository(SessionFactory))
    with pytest.raises(ValueError, match="matching predecessor"):
        await finalizer.execute(aid, None)
    async with SessionFactory() as s:
        assert (await s.get(MonitoringRun, run_id)).status.value == "running"
        assert (await s.get(MonitoringJob, job_id)).status.value == "analyzing"


@pytest.mark.asyncio
async def test_source_transient_exhaustion_is_durable_not_lost_transport():
    from app.modules.analysis.infrastructure.source_analysis_repository import SqlAlchemySourceAnalysisRepository
    from app.modules.analysis.infrastructure.source_analysis_result_repository import SqlAlchemySourceAnalysisResultRepository
    from app.modules.analysis.application.source_analysis_use_cases import ExecuteSourceAnalysisUseCase
    from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec
    from app.models.source_analysis import OrmSourceAnalysis
    job_id, run_id, _ = await fixture_aggregation()
    async with SessionFactory() as s:
        job = await SqlAlchemyMonitoringJobRepository(s).get_by_id(job_id, include_sources=True)
        task = SourceTask(run_id=run_id, source_id=job.sources[0].id, ordinal=1)
        s.add(task)
        await s.flush()
        repo = SqlAlchemySourceAnalysisRepository(s)
        a = await repo.ensure_for_source_task(task.id, run_id, task.source_id, SourceAnalysisSpec())
        await s.commit()
        use_case = ExecuteSourceAnalysisUseCase(repo, SqlAlchemySourceAnalysisResultRepository(s),
            AsyncMock(get_documents_for_task=AsyncMock(return_value=[])),
            AsyncMock(execute=AsyncMock(side_effect=TransientAnalysisError("private-content"))))
        for _ in range(2):
            with pytest.raises(TransientAnalysisError):
                await use_case.execute(a.id)
        assert (await use_case.execute(a.id))["status"] == "failed"
        row = await s.get(OrmSourceAnalysis, a.id, populate_existing=True)
        assert row.attempt_count == 3 and row.error_code == "ANALYSIS_RETRY_EXHAUSTED"
        assert row.claim_token is None and row.lease_expires_at is None
        assert "private-content" not in row.error_message
