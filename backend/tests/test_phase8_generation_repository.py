"""Isolated PostgreSQL proofs for request identity and fenced claim ownership."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from tempfile import TemporaryDirectory
import time
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.bot_identity import BotIdentity
from app.models.completion_tracker import OrmRunCompletionTracker
from app.models.enums import JobStatus
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_job import MonitoringJob
from app.models.monitoring_run import MonitoringRun
from app.models.report_generation import OrmReportArtifact, OrmReportGeneration
from app.models.run_aggregation import OrmAggregatedAnalysisResult, OrmRunAggregation
from app.models.user import User
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.analysis.domain.aggregate_report_snapshot import freeze_report_snapshot
from app.modules.reports.infrastructure.generation_repository import (
    ReportClaimKind, SqlAlchemyReportGenerationRepository,
)
from app.modules.reports.application.execute_generation import ExecuteReportGeneration
from app.modules.reports.infrastructure.legacy_exporter import LegacyReportRenderer
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage
from app.modules.reports.infrastructure.terminal_reader import ReportTerminalReader
from app.modules.monitoring.application.finalize_report import FinalizeMonitoringReport
from app.modules.monitoring.infrastructure.report_finalization_repository import MonitoringReportFinalizationRepository
from app.shared.integration.outbox import enqueue


engine = create_async_engine(settings.database_url, poolclass=NullPool)
sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def seed_ready() -> tuple[object, object]:
    async with sessions() as session:
        user = await session.scalar(select(User).limit(1))
        bot = await session.scalar(select(BotIdentity).limit(1))
        assert user is not None and bot is not None
        job = MonitoringJob(
            id=uuid4(), topic="گزارش پایدار", date_from_jalali="1405/07/01",
            date_to_jalali="1405/07/08", date_from_gregorian=date(2026, 9, 23),
            date_to_gregorian=date(2026, 9, 30), requested_by=user.id,
            bot_identity_id=bot.id, status=JobStatus.ANALYZING,
        )
        session.add(job)
        await session.flush()
        run = MonitoringRun(
            id=uuid4(), monitoring_job_id=job.id, execution_mode="distributed",
            status=MonitoringRunStatus.RUNNING, expected_sources=0,
        )
        session.add(run)
        await session.flush()
        aggregation = OrmRunAggregation(
            id=uuid4(), run_id=run.id, status="completed",
            readiness_reason="all_terminal", manifest_digest="a" * 64,
            run_analysis_spec_digest="b" * 64,
        )
        tracker = OrmRunCompletionTracker(
            id=uuid4(), run_id=run.id, status="analysis_ready",
            analysis_terminal_count=0, analysis_successful_count=0,
            analysis_failed_count=0, analysis_cancelled_count=0,
            aggregation_id=aggregation.id,
        )
        session.add_all([aggregation, tracker])
        await session.flush()
        result = OrmAggregatedAnalysisResult(
            id=uuid4(), aggregation_id=aggregation.id, run_id=run.id,
            schema_version="1.0", manifest_digest="a" * 64,
            manifest_schema_version="2.0", readiness_reason="all_terminal",
            expected_sources=0, successful_sources=0, failed_sources=0,
            cancelled_sources=0, analysis_successful=0, analysis_failed=0,
            analysis_cancelled=0, summary="هیچ تحلیل موفقی موجود نیست.",
            findings=[], coverage_metadata={}, provenance_references=[],
            raw_result={"status": "zero_inputs", "report_snapshot": freeze_report_snapshot("هیچ تحلیل موفقی موجود نیست.", [], [])},
        )
        session.add(result)
        await session.commit()
        return run.id, result.id


@pytest.mark.asyncio
async def test_ten_duplicate_handoffs_create_one_generation_and_dispatch() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    identifiers = await asyncio.gather(*(
        repository.ensure_from_analysis_ready(run_id, result_id, None)
        for _ in range(10)
    ))
    assert len(set(identifiers)) == 1
    async with sessions() as session:
        generation_count = await session.scalar(
            select(func.count()).select_from(OrmReportGeneration)
            .where(OrmReportGeneration.run_id == run_id)
        )
        dispatch_count = await session.scalar(
            select(func.count()).select_from(IntegrationOutbox)
            .where(IntegrationOutbox.message_key == f"report-generation:{identifiers[0]}")
        )
        assert generation_count == dispatch_count == 1


@pytest.mark.asyncio
async def test_claim_heartbeat_and_stale_owner_fencing() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    assert generation_id is not None
    now = datetime.now(UTC)
    token_a, token_b = uuid4(), uuid4()
    first = await repository.claim(generation_id, token_a, 30, now)
    assert first.kind == ReportClaimKind.CLAIMED
    duplicate = await repository.claim(generation_id, token_b, 30, now + timedelta(seconds=2))
    assert duplicate.kind == ReportClaimKind.ACTIVE_LEASE
    assert await repository.renew_lease(generation_id, token_a, 30, now + timedelta(seconds=10))
    still_active = await repository.claim(generation_id, token_b, 30, now + timedelta(seconds=31))
    assert still_active.kind == ReportClaimKind.ACTIVE_LEASE
    second = await repository.claim(generation_id, token_b, 30, now + timedelta(seconds=41))
    assert second.kind == ReportClaimKind.CLAIMED
    assert second.attempt_count == 2
    assert not await repository.renew_lease(generation_id, token_a, 30, now + timedelta(seconds=42))
    assert not await repository.accept_artifact(
        generation_id, token_a, export_format="pdf",
        storage_key=f"{generation_id}-{'a' * 64}.pdf",
        checksum="a" * 64, size=20,
        schema_version="1.0", template_version="1.0",
    )
    assert not await repository.fail_permanently(generation_id, token_a, "StaleWorker")
    async with sessions() as session:
        generation = await session.get(OrmReportGeneration, generation_id)
        assert generation is not None
        assert generation.status == "running"
        assert generation.claim_token == token_b


@pytest.mark.asyncio
async def test_real_render_duplicate_tasks_and_terminal_finalization() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    with TemporaryDirectory() as directory:
        use_case = ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory))
        outcomes = await asyncio.gather(*(use_case.execute(generation_id) for _ in range(10)))
        assert sum(o["status"] == "completed" for o in outcomes) == 1
        assert {o["status"] for o in outcomes} <= {"completed", "active_lease", "already_terminal"}
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            assert run.status == MonitoringRunStatus.RUNNING
            artifacts = (await session.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id))).all()
            assert {a.format for a in artifacts} == {"pdf", "docx"}
            assert await session.scalar(select(func.count()).select_from(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{generation_id}")) == 1
        finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
        finals = await asyncio.gather(*(finalizer.execute(generation_id, None) for _ in range(10)))
        assert sum(o["status"] == "finalized" for o in finals) == 1
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            finished = run.finished_at
            assert run.status == MonitoringRunStatus.COMPLETED
        await finalizer.execute(generation_id, None)
        async with sessions() as session:
            assert (await session.get(MonitoringRun, run_id)).finished_at == finished


@pytest.mark.asyncio
async def test_synchronous_slow_render_keeps_heartbeat_alive() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)

    started = Event()
    class SlowRenderer:
        def render(self, request):
            started.set()
            time.sleep(0.6)
            return LegacyReportRenderer().render(request)

    with TemporaryDirectory() as directory:
        task = asyncio.create_task(ExecuteReportGeneration(repository, SlowRenderer(), LocalReportFileStorage(directory), lease_seconds=2, heartbeat_seconds=0.05).execute(generation_id))
        assert await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.20)
        async with sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            assert generation.lease_expires_at > generation.claimed_at + timedelta(seconds=2.05)
        duplicate = await repository.claim(generation_id, uuid4(), 2, datetime.now(UTC))
        assert duplicate.kind == ReportClaimKind.ACTIVE_LEASE
        assert (await task)["status"] == "completed"


@pytest.mark.asyncio
async def test_partial_format_transient_retry_reuses_pdf() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)

    class FailDocxOnce:
        calls = {"pdf": 0, "docx": 0}
        def render(self, request):
            fmt = request.export_format.value
            self.calls[fmt] += 1
            if fmt == "docx" and self.calls[fmt] == 1:
                raise OSError("temporary dependency unavailable")
            return LegacyReportRenderer().render(request)

    renderer = FailDocxOnce()
    with TemporaryDirectory() as directory:
        use_case = ExecuteReportGeneration(repository, renderer, LocalReportFileStorage(directory))
        with pytest.raises(OSError):
            await use_case.execute(generation_id)
        async with sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            assert generation.status == "pending"
            assert await session.scalar(select(func.count()).select_from(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id)) == 1
        assert (await use_case.execute(generation_id))["status"] == "completed"
        assert renderer.calls == {"pdf": 1, "docx": 2}


@pytest.mark.asyncio
async def test_cancel_during_render_cannot_accept_stale_success() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)

    started = Event()
    class SlowRenderer:
        def render(self, request):
            started.set()
            time.sleep(0.6)
            return LegacyReportRenderer().render(request)

    with TemporaryDirectory() as directory:
        use_case = ExecuteReportGeneration(repository, SlowRenderer(), LocalReportFileStorage(directory), lease_seconds=2, heartbeat_seconds=0.05)
        task = asyncio.create_task(use_case.execute(generation_id))
        assert await asyncio.to_thread(started.wait, 5)
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            run.status = MonitoringRunStatus.CANCELLED
            await session.commit()
        assert (await task)["status"] == "claim_lost"
        assert (await use_case.execute(generation_id))["status"] == "run_cancelled"
        async with sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            assert generation.status == "cancelled"
            assert generation.completed_at is None
            assert await session.scalar(select(func.count()).select_from(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["before_render", "after_file", "after_metadata", "after_terminal_commit"])
async def test_file_crash_windows_resume_one_required_set(window) -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    crashed = False
    render_calls = 0

    class Renderer:
        def render(self, request):
            nonlocal crashed, render_calls
            render_calls += 1
            if window == "before_render" and not crashed:
                crashed = True
                raise OSError("crash before file")
            return LegacyReportRenderer().render(request)

    class CrashRepository:
        def __getattr__(self, name):
            return getattr(repository, name)
        async def accept_artifact(self, *args, **kwargs):
            nonlocal crashed
            if window == "after_file" and not crashed:
                crashed = True
                raise OSError("crash after atomic file write")
            return await repository.accept_artifact(*args, **kwargs)
        async def complete(self, *args, **kwargs):
            nonlocal crashed
            if window == "after_metadata" and not crashed:
                crashed = True
                raise OSError("crash after artifact metadata")
            result = await repository.complete(*args, **kwargs)
            if window == "after_terminal_commit" and not crashed:
                crashed = True
                raise OSError("crash after terminal commit")
            return result

    with TemporaryDirectory() as directory:
        storage = LocalReportFileStorage(directory)
        runner = ExecuteReportGeneration(CrashRepository(), Renderer(), storage)
        with pytest.raises(OSError):
            await runner.execute(generation_id)
        rendered_before_retry = render_calls
        assert (await runner.execute(generation_id))["status"] in {"completed", "already_terminal"}
        if window in {"after_metadata", "after_terminal_commit"}:
            assert render_calls == rendered_before_retry
        async with sessions() as session:
            rows = (await session.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id))).all()
            assert len(rows) == 2
            assert all(storage.verify(generation_id, r.format, r.storage_key, r.content_checksum, r.content_size) for r in rows)
            assert (await session.get(OrmReportGeneration, generation_id)).status == "completed"
            assert await session.scalar(select(func.count()).select_from(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{generation_id}")) == 1


@pytest.mark.asyncio
async def test_permanent_renderer_failure_closes_run_with_durable_terminal() -> None:
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)

    class InvalidRenderer:
        def render(self, request):
            raise ValueError("secret content must not be persisted")

    with TemporaryDirectory() as directory:
        runner = ExecuteReportGeneration(repository, InvalidRenderer(), LocalReportFileStorage(directory))
        assert (await runner.execute(generation_id))["status"] == "failed"
        assert (await runner.execute(generation_id))["status"] == "already_terminal"
        finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
        assert (await finalizer.execute(generation_id, None))["status"] == "finalized"
        async with sessions() as session:
            generation = await session.get(OrmReportGeneration, generation_id)
            assert generation.status == "failed"
            assert generation.error_code == "PermanentReportRenderError"
            assert "secret" not in (generation.sanitized_error or "")
            assert (await session.get(MonitoringRun, run_id)).status == MonitoringRunStatus.FAILED


@pytest.mark.asyncio
async def test_pending_cancel_and_post_render_cancel_are_terminal_safe() -> None:
    repository = SqlAlchemyReportGenerationRepository(sessions)
    run_id, result_id = await seed_ready()
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    async with sessions() as session:
        run = await session.get(MonitoringRun, run_id)
        run.status = MonitoringRunStatus.CANCELLED
        await session.commit()
    with TemporaryDirectory() as directory:
        runner = ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory))
        assert (await runner.execute(generation_id))["status"] == "run_cancelled"
    run_id, result_id = await seed_ready()
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    with TemporaryDirectory() as directory:
        assert (await ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory)).execute(generation_id))["status"] == "completed"
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            run.status = MonitoringRunStatus.CANCELLED
            run.finished_at = datetime.now(UTC)
            finished = run.finished_at
            await session.commit()
        finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
        assert (await finalizer.execute(generation_id, None))["status"] == "already_final"
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            assert run.status == MonitoringRunStatus.CANCELLED
            assert run.finished_at == finished


async def handoff_intent(run_id, result_id):
    async with sessions() as session:
        await enqueue(session,
            message_key=f"report-generation-handoff:{run_id}:{result_id}",
            message_type="REPORT_GENERATION_HANDOFF",
            payload={"schema_version": 1, "run_id": str(run_id), "result_id": str(result_id)})
        await session.commit()
        return await session.scalar(select(IntegrationOutbox.id).where(
            IntegrationOutbox.message_key == f"report-generation-handoff:{run_id}:{result_id}"))


@pytest.mark.asyncio
async def test_invalid_spec_fails_atomically_without_dispatch_or_infinite_retry():
    run_id, result_id = await seed_ready()
    async with sessions() as session:
        run = await session.get(MonitoringRun, run_id)
        job = await session.get(MonitoringJob, run.monitoring_job_id)
        job.topic = "   "
        await session.commit()
    outbox_id = await handoff_intent(run_id, result_id)
    repository = SqlAlchemyReportGenerationRepository(sessions)
    ids = await asyncio.gather(*(repository.ensure_from_analysis_ready(run_id, result_id, outbox_id) for _ in range(10)))
    assert len(set(ids)) == 1
    async with sessions() as session:
        generation = await session.get(OrmReportGeneration, ids[0])
        assert generation.status == "failed"
        assert generation.attempt_count == 0
        assert generation.spec_json["title"] == "   "  # no silent truncation/repair
        assert generation.error_code == "InvalidImmutableReportSpec"
        assert (await session.get(IntegrationOutbox, outbox_id)).consumer_acked_at
        assert await session.scalar(select(func.count()).select_from(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-generation:{ids[0]}")) == 0
        terminal = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{ids[0]}"))
        assert terminal is not None
    finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
    assert (await finalizer.execute(ids[0], terminal.id))["status"] == "finalized"
    assert await repository.ensure_from_analysis_ready(run_id, result_id, outbox_id) == ids[0]


@pytest.mark.asyncio
async def test_cancel_before_handoff_does_not_create_report():
    run_id, result_id = await seed_ready()
    async with sessions() as session:
        run = await session.get(MonitoringRun, run_id)
        run.status = MonitoringRunStatus.CANCELLED
        await session.commit()
    outbox_id = await handoff_intent(run_id, result_id)
    repository = SqlAlchemyReportGenerationRepository(sessions)
    assert await repository.ensure_from_analysis_ready(run_id, result_id, outbox_id) is None
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(OrmReportGeneration).where(OrmReportGeneration.run_id == run_id)) == 0
        assert (await session.get(IntegrationOutbox, outbox_id)).consumer_acked_at is not None


@pytest.mark.asyncio
async def test_finalized_redelivery_keeps_spec_and_terminal_timestamp():
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    with TemporaryDirectory() as directory:
        assert (await ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory)).execute(generation_id))["status"] == "completed"
    finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
    assert (await finalizer.execute(generation_id, None))["status"] == "finalized"
    async with sessions() as session:
        run = await session.get(MonitoringRun, run_id)
        timestamp = run.finished_at
        generation = await session.get(OrmReportGeneration, generation_id)
        spec_digest = generation.spec_digest
        job = await session.get(MonitoringJob, run.monitoring_job_id)
        job.topic = "mutable newer title"
        await session.commit()
    assert await repository.ensure_from_analysis_ready(run_id, result_id, None) == generation_id
    async with sessions() as session:
        assert (await session.get(MonitoringRun, run_id)).finished_at == timestamp
        assert (await session.get(OrmReportGeneration, generation_id)).spec_digest == spec_digest


@pytest.mark.asyncio
async def test_handoff_and_finalizer_ack_rollback(monkeypatch):
    from app.modules.reports.infrastructure import generation_repository as reporting
    from app.modules.monitoring.infrastructure import report_finalization_repository as monitoring
    run_id, result_id = await seed_ready()
    outbox_id = await handoff_intent(run_id, result_id)
    repository = SqlAlchemyReportGenerationRepository(sessions)
    real_ack = reporting.acknowledge_in_session
    async def crash_after_ack(session, identifier):
        await real_ack(session, identifier)
        await session.flush()
        raise OSError("isolated precommit injection")
    monkeypatch.setattr(reporting, "acknowledge_in_session", crash_after_ack)
    with pytest.raises(OSError):
        await repository.ensure_from_analysis_ready(run_id, result_id, outbox_id)
    async with sessions() as session:
        assert await session.scalar(select(func.count()).select_from(OrmReportGeneration).where(OrmReportGeneration.run_id == run_id)) == 0
        assert (await session.get(IntegrationOutbox, outbox_id)).consumer_acked_at is None
    monkeypatch.setattr(reporting, "acknowledge_in_session", real_ack)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, outbox_id)
    with TemporaryDirectory() as directory:
        assert (await ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory)).execute(generation_id))["status"] == "completed"
    async with sessions() as session:
        terminal = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{generation_id}"))
    finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
    monkeypatch.setattr(monitoring, "acknowledge_in_session", crash_after_ack)
    with pytest.raises(OSError):
        await finalizer.execute(generation_id, terminal.id)
    async with sessions() as session:
        assert (await session.get(MonitoringRun, run_id)).status == MonitoringRunStatus.RUNNING
        assert (await session.get(IntegrationOutbox, terminal.id)).consumer_acked_at is None
    monkeypatch.setattr(monitoring, "acknowledge_in_session", real_ack)
    assert (await finalizer.execute(generation_id, terminal.id))["status"] == "finalized"


@pytest.mark.asyncio
async def test_ten_analysis_ready_control_deliveries_create_one_handoff():
    from app.modules.monitoring.infrastructure.completion_tracker_repository import SqlAlchemyRunCompletionTrackerRepository
    run_id, result_id = await seed_ready()
    async with sessions() as session:
        tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
        tracker.status = "aggregation_dispatched"
        payload = {"schema_version": 1, "event": "analysis_ready", "run_id": str(run_id),
                   "aggregation_id": str(tracker.aggregation_id), "result_id": str(result_id)}
        await enqueue(session, message_key=f"phase8-test-ready:{result_id}", message_type="ANALYSIS_READY", payload=payload)
        await session.commit()
        outbox_id = await session.scalar(select(IntegrationOutbox.id).where(IntegrationOutbox.message_key == f"phase8-test-ready:{result_id}"))
    controls = SqlAlchemyRunCompletionTrackerRepository(sessions)
    results = await asyncio.gather(*(controls.apply_outbox_event(payload, outbox_id, datetime.now(UTC)) for _ in range(10)))
    assert all(r["status"] == "analysis_ready" for r in results)
    async with sessions() as session:
        tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
        timestamp = tracker.analysis_ready_at
        assert timestamp is not None
        handoffs = (await session.scalars(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-generation-handoff:{run_id}:{result_id}"))).all()
        assert len(handoffs) == 1
        assert (await session.get(IntegrationOutbox, outbox_id)).consumer_acked_at is not None
    await controls.apply_outbox_event(payload, outbox_id, datetime.now(UTC))
    async with sessions() as session:
        assert (await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))).analysis_ready_at == timestamp
    repository = SqlAlchemyReportGenerationRepository(sessions)
    ids = await asyncio.gather(*(repository.ensure_from_analysis_ready(run_id, result_id, handoffs[0].id) for _ in range(10)))
    assert len(set(ids)) == 1


@pytest.mark.asyncio
async def test_stale_owner_cannot_write_after_replacement_completes():
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    stale_token = uuid4()
    assert (await repository.claim(generation_id, stale_token, 1, datetime.now(UTC))).kind == ReportClaimKind.CLAIMED
    await asyncio.sleep(1.1)
    with TemporaryDirectory() as directory:
        assert (await ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory)).execute(generation_id))["status"] == "completed"
        async with sessions() as session:
            artifacts = (await session.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id))).all()
            old_checksums = {a.format: a.content_checksum for a in artifacts}
        assert not await repository.accept_artifact(generation_id, stale_token, export_format="pdf", storage_key=f"{generation_id}-{'c' * 64}.pdf", checksum="c" * 64, size=10, schema_version="1.0", template_version="1.0")
        assert not await repository.complete(generation_id, stale_token, {}, full_report={}, sources=[])
        assert not await repository.fail_permanently(generation_id, stale_token, "LateFailure")
        async with sessions() as session:
            assert (await session.get(OrmReportGeneration, generation_id)).attempt_count == 2
            artifacts = (await session.scalars(select(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id))).all()
            assert {a.format: a.content_checksum for a in artifacts} == old_checksums
            assert await session.scalar(select(func.count()).select_from(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{generation_id}")) == 1


@pytest.mark.asyncio
async def test_database_input_guards_and_retry_snapshot_are_immutable():
    from sqlalchemy.exc import DBAPIError
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    token = uuid4()
    await repository.claim(generation_id, token, 120, datetime.now(UTC))
    before = await repository.load_input(generation_id, token)
    async with sessions() as session:
        result = await session.get(OrmAggregatedAnalysisResult, result_id)
        result.summary = "late mutation forbidden"
        with pytest.raises(DBAPIError):
            await session.commit()
        await session.rollback()
        generation = await session.get(OrmReportGeneration, generation_id)
        generation.spec_json = {**generation.spec_json, "title": "changed"}
        with pytest.raises(DBAPIError):
            await session.commit()
        await session.rollback()
        run = await session.get(MonitoringRun, run_id)
        run.expected_sources = 99  # live counters are not report input
        job = await session.get(MonitoringJob, run.monitoring_job_id)
        job.topic = "later job metadata"
        await session.commit()
    await repository.release_for_retry(generation_id, token)
    next_token = uuid4()
    await repository.claim(generation_id, next_token, 120, datetime.now(UTC))
    after = await repository.load_input(generation_id, next_token)
    assert before == after


@pytest.mark.asyncio
async def test_closed_cancelled_tracker_does_not_orphan_terminal_ack():
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    async with sessions() as session:
        run = await session.get(MonitoringRun, run_id)
        run.status = MonitoringRunStatus.CANCELLED
        tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
        tracker.status = "cancelled"
        await session.commit()
    assert (await repository.claim(generation_id, uuid4(), 120, datetime.now(UTC))).kind == ReportClaimKind.RUN_CANCELLED
    async with sessions() as session:
        terminal = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == f"report-terminal:{generation_id}"))
    finalizer = FinalizeMonitoringReport(ReportTerminalReader(sessions), MonitoringReportFinalizationRepository(sessions))
    assert (await finalizer.execute(generation_id, terminal.id))["status"] == "already_final"
    async with sessions() as session:
        assert (await session.get(IntegrationOutbox, terminal.id)).consumer_acked_at is not None
        assert (await session.get(MonitoringRun, run_id)).status == MonitoringRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_report_ownership_and_historical_job_delete_compatibility():
    from sqlalchemy import delete
    from app.models.analysis_report import AnalysisReport
    from app.modules.reports.infrastructure.repository import SqlAlchemyReportRepository
    run_id, result_id = await seed_ready()
    repository = SqlAlchemyReportGenerationRepository(sessions)
    generation_id = await repository.ensure_from_analysis_ready(run_id, result_id, None)
    with TemporaryDirectory() as directory:
        assert (await ExecuteReportGeneration(repository, LegacyReportRenderer(), LocalReportFileStorage(directory)).execute(generation_id))["status"] == "completed"
        async with sessions() as session:
            run = await session.get(MonitoringRun, run_id)
            report = await session.scalar(select(AnalysisReport).where(AnalysisReport.job_id == run.monitoring_job_id))
            public_repository = SqlAlchemyReportRepository(session)
            assert await public_repository.get_accessible(report.id, report.created_by, "user") is not None
            assert await public_repository.get_accessible(report.id, uuid4(), "user") is None
            assert await public_repository.get_accessible(report.id, uuid4(), "super_admin") is not None
            await session.execute(delete(MonitoringJob).where(MonitoringJob.id == run.monitoring_job_id))
            await session.commit()
        async with sessions() as session:
            assert await session.get(OrmReportGeneration, generation_id) is None
            assert await session.scalar(select(func.count()).select_from(OrmReportArtifact).where(OrmReportArtifact.report_generation_id == generation_id)) == 0
