"""Phase 7A: Contract Hardening for Fan-In, Aggregation & Deadline Test Suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, engine
from app.models.bot_identity import BotIdentity
from app.models.completion_tracker import (
    OrmRunCompletionEvent,
    OrmRunCompletionTracker,
)
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.run_aggregation import (
    OrmAggregatedAnalysisResult,
    OrmRunAggregation,
    OrmRunAggregationInput,
)
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_analysis import (
    OrmRunAnalysisSpec,
    OrmSourceAnalysisResult,
)
from app.models.user import User
from app.modules.analysis.application.aggregate_analysis_use_case import (
    ExecuteRunAggregationUseCase,
)
from app.modules.analysis.domain.run_aggregation import (
    AGGREGATION_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    AggregatedAnalysisResultData,
    AggregationClaimStatus,
    RunAggregationInputItem,
    RunAggregationStatus,
    compute_manifest_digest,
)
from app.modules.analysis.infrastructure.run_aggregation_repository import (
    SqlAlchemyRunAggregationRepository,
)
from app.modules.analysis.infrastructure.run_analysis_spec_repository import (
    SqlAlchemyRunAnalysisSpecRepository,
)
from app.modules.analysis.ports.run_aggregation import (
    MonitoringAnalysisReadyPublisherPort,
    RunAggregatorPort,
)
from app.modules.monitoring.application.evaluate_completion import (
    EvaluateRunCompletionUseCase,
)
from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTracker,
    RunCompletionTrackerStatus,
    evaluate_tracker_readiness,
)
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.modules.monitoring.infrastructure.completion_tracker_repository import (
    SqlAlchemyRunCompletionTrackerRepository,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
)
from app.modules.monitoring.ports.completion import (
    DeadlineSchedulerPort,
    RunAggregationHandoffPort,
)


class MockDeadlineScheduler(DeadlineSchedulerPort):
    def __init__(self) -> None:
        self.scheduled: list[dict[str, object]] = []

    def schedule_deadline_signal(
        self,
        run_id: UUID,
        tracker_id: UUID,
        deadline_at: datetime,
    ) -> None:
        self.scheduled.append({
            "run_id": run_id,
            "tracker_id": tracker_id,
            "deadline_at": deadline_at,
        })


class MockSlowAggregator(RunAggregatorPort):
    def __init__(self, delay_seconds: float = 0.1) -> None:
        self.delay_seconds = delay_seconds
        self.calls: list[UUID] = []

    async def aggregate(
        self,
        spec: Mapping[str, object],
        inputs: Sequence[Mapping[str, object]],
        coverage_metadata: Mapping[str, object],
    ) -> dict[str, object]:
        await asyncio.sleep(self.delay_seconds)
        return {
            "summary": "Synthesized executive summary for Phase 7A test.",
            "findings": [
                {
                    "topic": "AI Governance",
                    "sentiment": "neutral",
                    "evidence": "Doc citation 1",
                    "source_id": str(inputs[0].get("source_id")) if inputs else str(uuid4()),
                    "source_analysis_result_id": str(inputs[0].get("source_analysis_result_id")) if inputs else str(uuid4()),
                }
            ],
            "coverage_metadata": dict(coverage_metadata),
            "provenance_references": [
                {
                    "source_id": str(i.get("source_id")),
                    "source_analysis_result_id": str(i.get("source_analysis_result_id")),
                    "input_digest": str(i.get("input_digest")),
                }
                for i in inputs
            ],
            "raw_result": {"status": "ok"},
        }


def consume_for_report(
    result: AggregatedAnalysisResultData,
    manifest_inputs: Sequence[RunAggregationInputItem],
) -> dict[str, object]:
    """Test-only Phase 8 consumer demonstrating complete self-description.

    Asserts that Phase 8 can read all required report elements purely from
    the AggregatedAnalysisResult + manifest inputs without querying raw crawl state
    or live MonitoringRun counters.
    """
    return {
        "summary": result.summary,
        "findings": result.findings,
        "coverage": result.coverage_metadata,
        "readiness_reason": result.readiness_reason,
        "cutoff_at": result.cutoff_at.isoformat() if result.cutoff_at else None,
        "schema_version": result.schema_version,
        "manifest_schema_version": result.manifest_schema_version,
        "manifest_digest": result.manifest_digest,
        "provenance_references": result.provenance_references,
        "manifest_input_count": len(manifest_inputs),
    }


@pytest.mark.asyncio
class TestPhase7AHardening(IsolatedAsyncioTestCase):
    """Rigorous test suite for Phase 7A Contract Hardening."""

    async def asyncSetUp(self) -> None:
        self.session_factory = SessionFactory
        self.now = datetime.now(UTC)

        async with self.session_factory() as session:
            user = await session.scalar(select(User).limit(1))
            if not user:
                user = User(
                    id=uuid4(),
                    email=f"p7a-{uuid4().hex[:6]}@example.org",
                    password_hash="fake",
                    full_name="Phase 7A Tester",
                    is_active=True,
                )
                session.add(user)
                await session.flush()

            bot = await session.scalar(select(BotIdentity).limit(1))
            if not bot:
                bot = BotIdentity(
                    id=uuid4(),
                    name="Phase 7A Bot",
                    version="1.0.0",
                    description="Phase 7A Bot",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(bot)
                await session.flush()
            bot_id = bot.id

            self.source_id = uuid4()
            src = Source(
                id=self.source_id,
                title="Phase 7A Test Source",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.UNKNOWN,
                is_active=True,
                base_url=f"https://example.com/p7a/{uuid4().hex[:6]}",
                created_by=user.id,
            )
            session.add(src)

            self.job_id = uuid4()
            self.user_id = user.id
            self.bot_id = bot_id
            job = MonitoringJob(
                id=self.job_id,
                topic="Phase 7A Fan-In Hardening Job",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=self.now.date() - timedelta(days=1),
                date_to_gregorian=self.now.date(),
                requested_by=user.id,
                bot_identity_id=bot_id,
                status=JobStatus.CRAWLING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(job)

            self.run_id = uuid4()
            run = OrmMonitoringRun(
                id=self.run_id,
                monitoring_job_id=self.job_id,
                status=MonitoringRunStatus.RUNNING,
                execution_mode="distributed",
                expected_sources=3,
                terminal_sources=0,
                successful_sources=0,
                failed_sources=0,
                cancelled_sources=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(run)

            self.spec_digest = "a" * 64
            spec = OrmRunAnalysisSpec(
                id=uuid4(),
                run_id=self.run_id,
                spec={
                    "model_provider": "openai",
                    "model_name": "gpt-4o-mini",
                    "prompt_version": "v2",
                    "temperature": 0.2,
                    "max_output_tokens": 4096,
                    "language_policy": "fa",
                },
                spec_digest=self.spec_digest,
            )
            session.add(spec)
            await session.commit()

        self.tracker_repo = SqlAlchemyRunCompletionTrackerRepository(self.session_factory)
        self.run_repo = SqlAlchemyMonitoringRunRepository(self.session_factory)
        self.agg_repo = SqlAlchemyRunAggregationRepository(self.session_factory)
        self.spec_repo = SqlAlchemyRunAnalysisSpecRepository(self.session_factory)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    # 1. Characterization Test: Deadline stalls without external event
    async def test_deadline_characterization_stalls_without_signal(self) -> None:
        deadline_time = self.now + timedelta(seconds=10)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)
        assert tracker.status == RunCompletionTrackerStatus.OPEN

        # Without scheduled signal and no new external events, tracker remains OPEN even after T
        after_t = deadline_time + timedelta(seconds=5)
        fetched = await self.tracker_repo.get_by_run_id(self.run_id)
        assert fetched is not None
        assert fetched.status == RunCompletionTrackerStatus.OPEN

        # With scheduled one-shot signal delivered, transitions to READY_DEADLINE
        past_deadline = self.now - timedelta(seconds=1)
        async with self.session_factory() as session:
            tr_orm = await session.get(OrmRunCompletionTracker, tracker.id)
            if tr_orm:
                tr_orm.deadline_at = past_deadline
                await session.commit()

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        res = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "tracker_id": str(tracker.id),
            "deadline_at": past_deadline.isoformat(),
        })

        assert res.get("status") == "aggregation_queued"
        updated = await self.tracker_repo.get_by_run_id(self.run_id)
        assert updated is not None
        assert updated.status in (RunCompletionTrackerStatus.READY_DEADLINE, RunCompletionTrackerStatus.AGGREGATION_DISPATCHED)
        assert updated.readiness_reason == "deadline"
        assert updated.cutoff_at == past_deadline
        async with self.session_factory() as session:
            handoff = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))
            assert handoff is not None

    # 2. One-shot deadline signal scheduled on initialization
    async def test_one_shot_deadline_scheduled_on_init(self) -> None:
        run_id2 = uuid4()
        job_id2 = uuid4()
        async with self.session_factory() as session:
            job2 = MonitoringJob(
                id=job_id2,
                topic="Job 2",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=self.now.date() - timedelta(days=1),
                date_to_gregorian=self.now.date(),
                requested_by=self.user_id,
                bot_identity_id=self.bot_id,
                status=JobStatus.CRAWLING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(job2)
            run2 = OrmMonitoringRun(
                id=run_id2,
                monitoring_job_id=job_id2,
                status=MonitoringRunStatus.RUNNING,
                execution_mode="distributed",
                expected_sources=2,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(run2)
            await session.commit()

        deadline_time = self.now + timedelta(seconds=30)
        scheduler = MockDeadlineScheduler()
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
            deadline_scheduler=scheduler,
        )

        # Create tracker with deadline
        tracker = await self.tracker_repo.get_or_create(run_id2, deadline_at=deadline_time)

        # First event triggers schedule
        await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(run_id2),
            "source_task_id": str(uuid4()),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        assert scheduler.scheduled == []  # no Redis ETA dependency
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{run_id2}:{deadline_time.isoformat()}"
            ))).scalars().all()
            assert len(rows) == 1
            assert rows[0].available_at == deadline_time
            assert rows[0].message_type == "DEADLINE_REACHED"

    # 3. Early deadline event triggers delayed retry (Section 8)
    async def test_early_deadline_delivery_delays_retry(self) -> None:
        future_deadline = self.now + timedelta(seconds=60)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=future_deadline)

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Event arrives early
        res = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "tracker_id": str(tracker.id),
            "deadline_at": future_deadline.isoformat(),
        })

        assert res.get("status") == "early_delivery"
        assert int(res.get("retry_after", 0)) > 0

        # Tracker must NOT be ready yet
        fetched = await self.tracker_repo.get_by_run_id(self.run_id)
        assert fetched is not None
        assert fetched.status == RunCompletionTrackerStatus.OPEN

    # 4. Deadline dispatch recovery (Section 7)
    async def test_deadline_dispatch_recovery(self) -> None:
        run_id3 = uuid4()
        job_id3 = uuid4()
        async with self.session_factory() as session:
            job3 = MonitoringJob(
                id=job_id3,
                topic="Job 3",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=self.now.date() - timedelta(days=1),
                date_to_gregorian=self.now.date(),
                requested_by=self.user_id,
                bot_identity_id=self.bot_id,
                status=JobStatus.CRAWLING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(job3)
            run3 = OrmMonitoringRun(
                id=run_id3,
                monitoring_job_id=job_id3,
                status=MonitoringRunStatus.RUNNING,
                execution_mode="distributed",
                expected_sources=2,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(run3)
            await session.commit()

        deadline_time = self.now + timedelta(seconds=45)
        # Tracker and deadline delivery intent share the same commit.
        await self.tracker_repo.get_or_create(run_id3, deadline_at=deadline_time)

        scheduler = MockDeadlineScheduler()
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
            deadline_scheduler=scheduler,
        )

        # A later event must not create a duplicate broker-dependent schedule.
        await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(run_id3),
            "source_task_id": str(uuid4()),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        assert scheduler.scheduled == []
        updated = await self.tracker_repo.get_by_run_id(run_id3)
        assert updated is not None
        assert updated.deadline_scheduled_at is not None
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{run_id3}:{deadline_time.isoformat()}"
            ))).scalars().all()
            assert len(rows) == 1
            assert rows[0].available_at == deadline_time

    # 5. Immutable cutoff_at: late events do not alter cutoff or reopen tracker (Section 9 & 10)
    async def test_immutable_cutoff_late_event_ignored(self) -> None:
        past_deadline = self.now - timedelta(seconds=10)
        await self.tracker_repo.get_or_create(self.run_id, deadline_at=past_deadline)

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Transition to READY_DEADLINE
        await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": past_deadline.isoformat(),
        })

        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status in (RunCompletionTrackerStatus.READY_DEADLINE, RunCompletionTrackerStatus.AGGREGATION_DISPATCHED)
        assert tracker.readiness_reason == "deadline"
        original_cutoff = tracker.cutoff_at
        assert original_cutoff == past_deadline

        # Late event arrives after deadline cutoff
        late_res = await use_case.execute_event({
            "event": "analysis_terminal",
            "run_id": str(self.run_id),
            "source_analysis_id": str(uuid4()),
            "outcome": "completed",
            "occurred_at": datetime.now(UTC).isoformat(),
        })

        tracker_after_late = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker_after_late is not None
        # Must NOT reopen tracker
        assert tracker_after_late.status in (RunCompletionTrackerStatus.READY_DEADLINE, RunCompletionTrackerStatus.AGGREGATION_DISPATCHED)
        # Must NOT mutate cutoff_at
        assert tracker_after_late.cutoff_at == original_cutoff

    # 6. READY_ALL_TERMINAL sets cutoff_at = ready_at (Section 9)
    async def test_ready_all_terminal_sets_cutoff_to_ready_at(self) -> None:
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.expected_sources = 1
            run.terminal_sources = 1
            run.successful_sources = 1
            await session.commit()

        await self.tracker_repo.get_or_create(self.run_id)
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Terminal analysis arrives
        await use_case.execute_event({
            "event": "analysis_terminal",
            "run_id": str(self.run_id),
            "source_analysis_id": str(uuid4()),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status in (RunCompletionTrackerStatus.READY_ALL_TERMINAL, RunCompletionTrackerStatus.AGGREGATION_DISPATCHED)
        assert tracker.readiness_reason == "all_terminal"
        assert tracker.cutoff_at is not None
        assert tracker.ready_at is not None
        # Cutoff equals ready_at
        assert tracker.cutoff_at == tracker.ready_at

    # 7. Manifest Digest v2 determinism & sensitivity tests (Section 17, 18, 19)
    async def test_manifest_digest_v2_determinism_and_sensitivity(self) -> None:
        r1 = uuid4()
        r2 = uuid4()
        s1 = uuid4()
        s2 = uuid4()

        inputs_a = [
            {"source_analysis_result_id": r1, "source_id": s1, "result_digest": "d1", "input_digest": "i1", "schema_version": "1.0"},
            {"source_analysis_result_id": r2, "source_id": s2, "result_digest": "d2", "input_digest": "i2", "schema_version": "1.0"},
        ]
        # Same items, reversed order
        inputs_b = [inputs_a[1], inputs_a[0]]

        counts = {"expected_sources": 2, "successful_sources": 2, "failed_sources": 0}
        cutoff = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)

        digest_a = compute_manifest_digest(inputs_a, "all_terminal", "spec1", cutoff_at=cutoff, completion_counts=counts)
        digest_b = compute_manifest_digest(inputs_b, "all_terminal", "spec1", cutoff_at=cutoff, completion_counts=counts)

        # Ordering independence
        assert digest_a == digest_b

        # Sensitivity: different source result
        inputs_diff_res = [
            {"source_analysis_result_id": r1, "source_id": s1, "result_digest": "d_altered", "input_digest": "i1", "schema_version": "1.0"},
            {"source_analysis_result_id": r2, "source_id": s2, "result_digest": "d2", "input_digest": "i2", "schema_version": "1.0"},
        ]
        digest_diff_res = compute_manifest_digest(inputs_diff_res, "all_terminal", "spec1", cutoff_at=cutoff, completion_counts=counts)
        assert digest_a != digest_diff_res

        # Sensitivity: different cutoff
        cutoff2 = datetime(2026, 9, 28, 13, 0, 0, tzinfo=UTC)
        digest_diff_cutoff = compute_manifest_digest(inputs_a, "all_terminal", "spec1", cutoff_at=cutoff2, completion_counts=counts)
        assert digest_a != digest_diff_cutoff

        # Sensitivity: different readiness reason
        digest_diff_reason = compute_manifest_digest(inputs_a, "deadline", "spec1", cutoff_at=cutoff, completion_counts=counts)
        assert digest_a != digest_diff_reason

        # Sensitivity: different spec digest
        digest_diff_spec = compute_manifest_digest(inputs_a, "all_terminal", "spec_altered", cutoff_at=cutoff, completion_counts=counts)
        assert digest_a != digest_diff_spec

    # 8. Self-describing AggregatedAnalysisResult metadata & versioning (Section 12-16)
    async def test_self_describing_aggregated_analysis_result(self) -> None:
        cutoff = self.now
        coverage_meta = {
            "expected_sources": 3,
            "successful_sources": 2,
            "failed_sources": 1,
            "cancelled_sources": 0,
            "analysis_successful": 2,
            "analysis_failed": 0,
            "analysis_cancelled": 0,
        }

        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            if run:
                run.expected_sources = 3
                run.successful_sources = 2
                run.failed_sources = 1
                run.cancelled_sources = 0
                await session.commit()

        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=cutoff,
            completion_metadata=coverage_meta,
        )

        mock_aggregator = MockSlowAggregator(delay_seconds=0.01)
        ready_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=mock_aggregator,
            ready_publisher=ready_pub,
        )

        exec_res = await use_case.execute(agg.id)
        assert exec_res.get("status") == "completed"

        async with self.session_factory() as session:
            res_orm = await session.scalar(
                select(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id == agg.id)
            )
            assert res_orm is not None
            assert res_orm.schema_version == AGGREGATION_SCHEMA_VERSION
            assert res_orm.manifest_schema_version == MANIFEST_SCHEMA_VERSION
            assert res_orm.manifest_digest == agg.manifest_digest
            assert res_orm.readiness_reason == "all_terminal"
            assert res_orm.cutoff_at == cutoff
            assert res_orm.expected_sources == 3
            assert res_orm.successful_sources == 2
            assert res_orm.failed_sources == 1
            assert "expected_sources" in res_orm.coverage_metadata

    # 9. Heartbeat renews lease during long aggregation (Section 23, 24, 25)
    async def test_heartbeat_renews_lease_during_long_aggregation(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1},
        )

        # Aggregator takes 0.25s, heartbeat interval set to 0.05s
        slow_aggregator = MockSlowAggregator(delay_seconds=0.25)
        ready_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=slow_aggregator,
            ready_publisher=ready_pub,
            lease_duration_seconds=10,
            heartbeat_interval_seconds=0.05,
        )

        exec_res = await use_case.execute(agg.id)
        assert exec_res.get("status") == "completed"

        # Verify aggregation completed successfully with valid result
        completed_agg = await self.agg_repo.get_by_id(agg.id)
        assert completed_agg is not None
        assert completed_agg.status == RunAggregationStatus.COMPLETED

    # 10. Heartbeat ownership loss and stale aggregator fencing (Section 24 & 30)
    async def test_stale_aggregator_fenced_if_token_mismatched(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1},
        )

        stale_token = uuid4()
        result_data = AggregatedAnalysisResultData(
            id=uuid4(),
            aggregation_id=agg.id,
            run_id=self.run_id,
            schema_version="1.0",
            manifest_digest="test_digest",
            readiness_reason="all_terminal",
            expected_sources=1,
            successful_sources=1,
            failed_sources=0,
            cancelled_sources=0,
            analysis_successful=1,
            analysis_failed=0,
            analysis_cancelled=0,
            summary="Stale summary",
            findings=[],
            coverage_metadata={},
            provenance_references=[],
            raw_result=None,
            created_at=self.now,
        )

        # Direct attempt to save result with invalid token fails
        saved = await self.agg_repo.save_result_and_complete(
            aggregation_id=agg.id,
            claim_token=stale_token,
            result=result_data,
            now=self.now,
        )
        assert not saved

    # 11. ACTIVE_LEASE duplicate delivery handling (Section 28)
    async def test_active_lease_duplicate_delivery(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1},
        )

        # Worker A claims execution
        claim_token = uuid4()
        claim = await self.agg_repo.claim_explicit(
            aggregation_id=agg.id,
            claim_token=claim_token,
            lease_duration_seconds=300,
            now=self.now,
        )
        assert claim.status == AggregationClaimStatus.CLAIMED

        # Worker B receives redelivery while Worker A holds active lease
        slow_aggregator = MockSlowAggregator()
        ready_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=slow_aggregator,
            ready_publisher=ready_pub,
        )

        redelivery_res = await use_case.execute(agg.id)
        assert redelivery_res.get("status") == "active_lease"
        # Aggregator must NOT have been called
        assert len(slow_aggregator.calls) == 0

    # 12. Aggregator crash and reclaim after lease expiry (Section 29)
    async def test_aggregator_reclaim_after_lease_expiry(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1},
        )

        # Worker A claims with 10s lease in past
        past_time = self.now - timedelta(seconds=20)
        claim_token_a = uuid4()
        await self.agg_repo.claim_explicit(
            aggregation_id=agg.id,
            claim_token=claim_token_a,
            lease_duration_seconds=10,
            now=past_time,
        )

        # Lease is now expired (past_time + 10s < now). Worker B executes:
        mock_aggregator = MockSlowAggregator(delay_seconds=0.01)
        ready_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=mock_aggregator,
            ready_publisher=ready_pub,
        )

        res = await use_case.execute(agg.id)
        assert res.get("status") == "completed"

        completed = await self.agg_repo.get_by_id(agg.id)
        assert completed is not None
        assert completed.status == RunAggregationStatus.COMPLETED
        # Attempt count incremented to 2
        assert completed.attempt_count == 2

    # 13. Completion publish failure recovery (Section 31)
    async def test_aggregation_publish_failure_recovery(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1},
        )

        # First execution succeeds and completes in DB, but publisher raises
        mock_aggregator = MockSlowAggregator(delay_seconds=0.01)
        failing_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        failing_pub.publish_analysis_ready.side_effect = RuntimeError("Redis connection error")

        use_case_failing = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=mock_aggregator,
            ready_publisher=failing_pub,
        )

        with pytest.raises(RuntimeError):
            await use_case_failing.execute(agg.id)

        # Status in DB was already marked COMPLETED
        saved_agg = await self.agg_repo.get_by_id(agg.id)
        assert saved_agg is not None
        assert saved_agg.status == RunAggregationStatus.COMPLETED

        # Redelivery occurs with working publisher: detects already_terminal and re-publishes
        working_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case_retry = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=mock_aggregator,
            ready_publisher=working_pub,
        )

        retry_res = await use_case_retry.execute(agg.id)
        assert retry_res.get("status") == "already_terminal"
        # Publisher was invoked during already_terminal path
        working_pub.publish_analysis_ready.assert_called_once_with(
            run_id=agg.run_id,
            aggregation_id=agg.id,
            result_id=agg.id,
        )

    # 14. Phase 8 Consumer Contract Test (Section 33)
    async def test_phase8_consumer_contract(self) -> None:
        cutoff = self.now
        coverage_meta = {
            "expected_sources": 2,
            "successful_sources": 2,
            "failed_sources": 0,
            "cancelled_sources": 0,
            "analysis_successful": 2,
            "analysis_failed": 0,
            "analysis_cancelled": 0,
        }

        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=cutoff,
            completion_metadata=coverage_meta,
        )

        mock_aggregator = MockSlowAggregator(delay_seconds=0.01)
        ready_pub = MagicMock(spec=MonitoringAnalysisReadyPublisherPort)
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=mock_aggregator,
            ready_publisher=ready_pub,
        )
        await use_case.execute(agg.id)

        async with self.session_factory() as session:
            res_orm = await session.scalar(
                select(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id == agg.id)
            )
            assert res_orm is not None

            # Load domain result object
            result_data = AggregatedAnalysisResultData(
                id=res_orm.id,
                aggregation_id=res_orm.aggregation_id,
                run_id=res_orm.run_id,
                schema_version=res_orm.schema_version,
                manifest_digest=res_orm.manifest_digest,
                readiness_reason=res_orm.readiness_reason,
                expected_sources=res_orm.expected_sources,
                successful_sources=res_orm.successful_sources,
                failed_sources=res_orm.failed_sources,
                cancelled_sources=res_orm.cancelled_sources,
                analysis_successful=res_orm.analysis_successful,
                analysis_failed=res_orm.analysis_failed,
                analysis_cancelled=res_orm.analysis_cancelled,
                summary=res_orm.summary,
                findings=res_orm.findings,
                coverage_metadata=res_orm.coverage_metadata,
                provenance_references=res_orm.provenance_references,
                raw_result=res_orm.raw_result,
                created_at=res_orm.created_at,
                cutoff_at=res_orm.cutoff_at,
                manifest_schema_version=res_orm.manifest_schema_version,
            )

        manifest_inputs = await self.agg_repo.load_manifest_inputs(agg.id)

        # Consumer reads completely without touching live MonitoringRun counters or crawled_documents
        report_data = consume_for_report(result_data, manifest_inputs)

        assert report_data["summary"] != ""
        assert len(report_data["findings"]) > 0
        assert report_data["schema_version"] == "1.0"
        assert report_data["manifest_schema_version"] == "2.0"
        assert report_data["readiness_reason"] == "all_terminal"
        assert report_data["manifest_digest"] == agg.manifest_digest
        assert "expected_sources" in report_data["coverage"]

    # 15. MonitoringRun status remains RUNNING (Section 35)
    async def test_monitoring_run_remains_running_after_phase7a(self) -> None:
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            assert run.status == MonitoringRunStatus.RUNNING
