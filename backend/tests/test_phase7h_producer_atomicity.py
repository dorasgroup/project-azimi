"""Failure-before-commit proofs for the four critical outbox producer families."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.bot_identity import BotIdentity
from app.models.completion_tracker import OrmRunCompletionTracker
from app.models.enums import JobStatus, SourceStatus, SourceType
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_job import MonitoringJob
from app.models.monitoring_run import MonitoringRun
from app.models.run_aggregation import OrmAggregatedAnalysisResult, OrmRunAggregation
from app.models.source import Source
from app.models.source_analysis import OrmSourceAnalysis
from app.models.source_task import SourceTask
from app.models.user import User
from app.modules.analysis.domain.run_aggregation import AggregatedAnalysisResultData
from app.modules.analysis.domain.source_analysis import SourceAnalysisStatus
from app.modules.analysis.infrastructure.run_aggregation_repository import SqlAlchemyRunAggregationRepository
from app.modules.analysis.infrastructure.source_analysis_repository import SqlAlchemySourceAnalysisRepository
from app.modules.monitoring.domain.completion_tracker import RunCompletionTrackerStatus
from app.modules.monitoring.domain.execution import MonitoringRunStatus, SourceTaskStatus
from app.modules.monitoring.infrastructure.completion_tracker_repository import SqlAlchemyRunCompletionTrackerRepository
from app.modules.monitoring.infrastructure.source_result_persister import SourceResultPersister


_engine = create_async_engine(settings.database_url, poolclass=NullPool)
Sessions = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


class FailCommitSession(AsyncSession):
    async def commit(self) -> None:
        raise RuntimeError("injected crash before commit")


FailingSessions = async_sessionmaker(_engine, expire_on_commit=False, class_=FailCommitSession)


async def _seed_family() -> dict[str, object]:
    now = datetime.now(UTC)
    async with Sessions() as session:
        user = await session.scalar(select(User).limit(1))
        bot = await session.scalar(select(BotIdentity).limit(1))
        assert user is not None and bot is not None, "Isolated test parents must be seeded"
        source = Source(
            id=uuid4(), title="Phase 7H atomicity source",
            base_url=f"https://atomic-{uuid4().hex}.example.invalid",
            type=SourceType.NEWS_SITE, last_status=SourceStatus.SUCCESS,
            created_by=user.id,
        )
        job = MonitoringJob(
            id=uuid4(), topic="Phase 7H atomicity", date_from_jalali="1405/01/01",
            date_to_jalali="1405/01/02", date_from_gregorian=date(2026, 3, 21),
            date_to_gregorian=date(2026, 3, 22), requested_by=user.id,
            bot_identity_id=bot.id, status=JobStatus.CRAWLING,
        )
        session.add_all([source, job])
        await session.flush()
        run = MonitoringRun(
            id=uuid4(), monitoring_job_id=job.id, execution_mode="distributed",
            status=MonitoringRunStatus.RUNNING, expected_sources=1,
        )
        session.add(run)
        await session.flush()
        source_token, analysis_token, aggregation_token = uuid4(), uuid4(), uuid4()
        task = SourceTask(
            id=uuid4(), run_id=run.id, source_id=source.id, ordinal=1,
            status=SourceTaskStatus.RUNNING, claim_token=source_token,
            claimed_at=now, lease_expires_at=now + timedelta(minutes=5),
            attempt_count=1, execution_spec={},
        )
        session.add(task)
        await session.flush()
        analysis = OrmSourceAnalysis(
            id=uuid4(), source_task_id=task.id, run_id=run.id, source_id=source.id,
            status=SourceAnalysisStatus.RUNNING, claim_token=analysis_token,
            claimed_at=now, lease_expires_at=now + timedelta(minutes=5),
            attempt_count=1, analysis_spec={},
        )
        tracker = OrmRunCompletionTracker(
            id=uuid4(), run_id=run.id, status=RunCompletionTrackerStatus.OPEN.value,
            analysis_terminal_count=0, analysis_successful_count=0,
            analysis_failed_count=0, analysis_cancelled_count=0,
        )
        aggregation = OrmRunAggregation(
            id=uuid4(), run_id=run.id, status="running", readiness_reason="all_terminal",
            manifest_digest="0" * 64, run_analysis_spec_digest="1" * 64,
            claim_token=aggregation_token, claimed_at=now,
            lease_expires_at=now + timedelta(minutes=5), attempt_count=1,
        )
        session.add_all([analysis, tracker, aggregation])
        await session.commit()
        return {
            "run": run.id, "job": job.id, "source": source.id, "task": task.id,
            "source_token": source_token, "analysis": analysis.id,
            "analysis_token": analysis_token, "aggregation": aggregation.id,
            "aggregation_token": aggregation_token,
        }


async def _outbox(session: AsyncSession, key: str) -> IntegrationOutbox | None:
    return await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == key))


@pytest.mark.asyncio
async def test_source_terminal_and_run_counter_roll_back_with_outbox() -> None:
    state = await _seed_family()

    class NoopFailureRecorder:
        async def record_failure(self, *_args: object) -> None:
            pass

    persister = SourceResultPersister(
        FailingSessions, lambda _session: None, lambda _session: None,
        lambda value: value, NoopFailureRecorder(),
    )
    with pytest.raises(RuntimeError, match="injected crash"):
        await persister.persist_failure(
            state["task"], state["source_token"], state["job"],
            state["source"], RuntimeError("isolated failure"),
        )
    async with Sessions() as session:
        task = await session.get(SourceTask, state["task"])
        run = await session.get(MonitoringRun, state["run"])
        assert task.status == SourceTaskStatus.RUNNING
        assert run.terminal_sources == 0 and run.failed_sources == 0
        assert await _outbox(session, f"source-terminal:{state['task']}") is None


@pytest.mark.asyncio
async def test_source_analysis_terminal_rolls_back_with_outbox() -> None:
    state = await _seed_family()
    with pytest.raises(RuntimeError, match="injected crash"):
        async with FailingSessions() as session:
            await SqlAlchemySourceAnalysisRepository(session).complete_analysis(
                state["analysis"], state["analysis_token"],
            )
    async with Sessions() as session:
        analysis = await session.get(OrmSourceAnalysis, state["analysis"])
        assert analysis.status == SourceAnalysisStatus.RUNNING
        assert await _outbox(session, f"analysis-terminal:{state['analysis']}") is None


@pytest.mark.asyncio
async def test_tracker_ready_rolls_back_with_aggregation_handoff() -> None:
    state = await _seed_family()
    now = datetime.now(UTC)
    repository = SqlAlchemyRunCompletionTrackerRepository(FailingSessions)
    with pytest.raises(RuntimeError, match="injected crash"):
        await repository.transition_to_ready(
            state["run"], RunCompletionTrackerStatus.OPEN,
            RunCompletionTrackerStatus.READY_ALL_TERMINAL, "all_terminal", now, now,
        )
    async with Sessions() as session:
        tracker = await session.scalar(select(OrmRunCompletionTracker).where(
            OrmRunCompletionTracker.run_id == state["run"]))
        assert tracker.status == RunCompletionTrackerStatus.OPEN.value
        assert await _outbox(session, f"run-aggregation-handoff:{state['run']}") is None


@pytest.mark.asyncio
async def test_aggregation_result_and_terminal_roll_back_with_analysis_ready() -> None:
    state = await _seed_family()
    now = datetime.now(UTC)
    result = AggregatedAnalysisResultData(
        id=uuid4(), aggregation_id=state["aggregation"], run_id=state["run"],
        schema_version="1.0", manifest_digest="0" * 64,
        readiness_reason="all_terminal", expected_sources=1, successful_sources=0,
        failed_sources=0, cancelled_sources=0, analysis_successful=0,
        analysis_failed=0, analysis_cancelled=0, summary="test", findings=[],
        coverage_metadata={}, provenance_references=[], raw_result={}, created_at=now,
    )
    repository = SqlAlchemyRunAggregationRepository(FailingSessions)
    with pytest.raises(RuntimeError, match="injected crash"):
        await repository.save_result_and_complete(
            state["aggregation"], state["aggregation_token"], result, now,
        )
    async with Sessions() as session:
        aggregation = await session.get(OrmRunAggregation, state["aggregation"])
        persisted_result = await session.scalar(select(OrmAggregatedAnalysisResult).where(
            OrmAggregatedAnalysisResult.aggregation_id == state["aggregation"]))
        assert aggregation.status == "running" and aggregation.claim_token == state["aggregation_token"]
        assert persisted_result is None
        assert await _outbox(session, f"analysis-ready:{state['aggregation']}") is None
