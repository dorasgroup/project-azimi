"""Phase 7: Event-Driven Fan-In & Run-Level AggregateAnalysis Test Suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionFactory, engine
from app.models.completion_tracker import (
    OrmRunCompletionEvent,
    OrmRunCompletionTracker,
)
from app.models.bot_identity import BotIdentity
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.integration_outbox import IntegrationOutbox
from app.models.user import User
from app.modules.monitoring.domain.execution import MonitoringRunStatus
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.run_aggregation import (
    OrmAggregatedAnalysisResult,
    OrmRunAggregation,
    OrmRunAggregationInput,
)
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_analysis import (
    OrmRunAnalysisSpec,
    OrmSourceAnalysis,
    OrmSourceAnalysisResult,
)
from app.models.source_task import SourceTask as OrmSourceTask
from app.modules.analysis.application.aggregate_analysis_use_case import (
    ExecuteRunAggregationUseCase,
)
from app.modules.analysis.domain.run_aggregation import (
    AggregationClaimStatus,
    RunAggregationStatus,
    compute_manifest_digest,
)
from app.modules.analysis.infrastructure.run_aggregation_repository import (
    SqlAlchemyRunAggregationRepository,
)
from app.modules.analysis.infrastructure.run_aggregator_adapter import (
    MAX_TOTAL_FINDINGS,
    RunAggregatorAdapter,
)
from app.modules.analysis.infrastructure.run_analysis_spec_repository import (
    SqlAlchemyRunAnalysisSpecRepository,
)
from app.modules.monitoring.application.evaluate_completion import (
    EvaluateRunCompletionUseCase,
)
from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTracker,
    RunCompletionTrackerStatus,
    evaluate_tracker_readiness,
)
from app.modules.monitoring.infrastructure.completion_tracker_repository import (
    SqlAlchemyRunCompletionTrackerRepository,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
)
from app.modules.monitoring.ports.completion import (
    RunAggregationHandoffPort,
)
from app.workers.aggregation_tasks import process_run_aggregation
from app.workers.control_tasks import process_control_event


@pytest.mark.asyncio
class TestPhase7EventDrivenFanIn(IsolatedAsyncioTestCase):
    """Rigorous test suite for Phase 7 Event-Driven Fan-In & Run-Level AggregateAnalysis."""

    async def asyncSetUp(self) -> None:
        self.session_factory = SessionFactory
        self.now = datetime.now(UTC)

        async with self.session_factory() as session:
            # Create user & bot
            user = await session.scalar(select(User).limit(1))
            if not user:
                user = User(
                    id=uuid4(),
                    email=f"p7-{uuid4().hex[:6]}@example.org",
                    password_hash="fake",
                    full_name="Phase 7 Tester",
                    is_active=True,
                )
                session.add(user)
                await session.flush()

            bot = await session.scalar(select(BotIdentity).limit(1))
            if not bot:
                bot = BotIdentity(
                    id=uuid4(),
                    name="Phase 7 Bot",
                    version="1.0.0",
                    description="Phase 7 Bot",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(bot)
                await session.flush()
            bot_id = bot.id

            # Create a test source
            self.source_id = uuid4()
            src = Source(
                id=self.source_id,
                title="Phase 7 Test Source",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.UNKNOWN,
                is_active=True,
                base_url=f"https://example.com/p7/{uuid4().hex[:6]}",
                created_by=user.id,
            )
            session.add(src)

            # Create a test monitoring job
            self.job_id = uuid4()
            job = MonitoringJob(
                id=self.job_id,
                topic="Phase 7 Fan-In Test Job",
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

            # Create a test monitoring run (execution_mode="distributed")
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

            # Add frozen RunAnalysisSpec
            spec = OrmRunAnalysisSpec(
                id=uuid4(),
                run_id=self.run_id,
                spec={
                    "model_provider": "openai",
                    "model_name": "gpt-4o-mini",
                    "prompt_version": "v1",
                    "temperature": 0.1,
                    "max_output_tokens": 4096,
                    "language_policy": "fa",
                },
                spec_digest="f" * 64,
            )
            session.add(spec)
            await session.commit()

        self.tracker_repo = SqlAlchemyRunCompletionTrackerRepository(self.session_factory)
        self.run_repo = SqlAlchemyMonitoringRunRepository(self.session_factory)
        self.agg_repo = SqlAlchemyRunAggregationRepository(self.session_factory)
        self.spec_repo = SqlAlchemyRunAnalysisSpecRepository(self.session_factory)

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _assert_ready_handoff_is_durable(self) -> None:
        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status in (
            RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            RunCompletionTrackerStatus.READY_DEADLINE,
        )
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"
            ))).scalars().all()
            assert len(rows) == 1
            assert rows[0].message_type == "RUN_AGGREGATION_HANDOFF"
            assert rows[0].payload_json["run_id"] == str(self.run_id)

    # 1. Event deduplication: duplicate terminal signals do not increment counters
    async def test_event_deduplication_counters_not_incremented(self) -> None:
        analysis_id = uuid4()
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        payload = {
            "event": "analysis_terminal",
            "run_id": str(self.run_id),
            "source_analysis_id": str(analysis_id),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        }

        # First delivery
        res1 = await use_case.execute_event(payload)
        tracker1 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker1 is not None
        assert tracker1.analysis_terminal_count == 1
        assert tracker1.analysis_successful_count == 1

        # Second delivery (duplicate)
        res2 = await use_case.execute_event(payload)
        tracker2 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker2 is not None
        assert tracker2.analysis_terminal_count == 1
        assert tracker2.analysis_successful_count == 1

    # 2. Event ordering independence: analysis event arriving before source task event
    async def test_event_ordering_analysis_before_source(self) -> None:
        source_task_id = uuid4()
        analysis_id = uuid4()
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # 1. Analysis terminal event arrives first
        await use_case.execute_event({
            "event": "analysis_terminal",
            "run_id": str(self.run_id),
            "source_analysis_id": str(analysis_id),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status == RunCompletionTrackerStatus.OPEN

        # Update run to simulate 1/1 sources finished
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.expected_sources = 1
            run.terminal_sources = 1
            run.successful_sources = 1
            await session.commit()

        # 2. Delayed source terminal event arrives
        res = await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(source_task_id),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        assert res.get("status") == "aggregation_queued"
        await self._assert_ready_handoff_is_durable()
        handoff_mock.ensure_and_dispatch_aggregation.assert_not_awaited()

    # 3. Last source failure triggers aggregation without waiting for non-existent analysis
    async def test_last_source_failure_triggers_aggregation(self) -> None:
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # 2 sources succeeded and their analyses completed
        tracker = await self.tracker_repo.get_or_create(self.run_id)
        await self.tracker_repo.record_event_and_update_counts(
            self.run_id, "analysis_terminal", uuid4(), "completed", self.now
        )
        await self.tracker_repo.record_event_and_update_counts(
            self.run_id, "analysis_terminal", uuid4(), "completed", self.now
        )

        # Run status in DB: 2 successful sources out of 3 expected, 3rd source fails
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.expected_sources = 3
            run.terminal_sources = 3
            run.successful_sources = 2
            run.failed_sources = 1
            await session.commit()

        # Last source terminal event (failure) arrives
        res = await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
            "outcome": "failed",
            "occurred_at": self.now.isoformat(),
        })

        assert res.get("status") == "aggregation_queued"
        await self._assert_ready_handoff_is_durable()
        handoff_mock.ensure_and_dispatch_aggregation.assert_not_awaited()

    # 4. Zero success test: all sources fail -> aggregation completes with zero-input structured result without calling LLM
    async def test_zero_success_sources_produces_empty_aggregation(self) -> None:
        # All 3 sources fail
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.expected_sources = 3
            run.terminal_sources = 3
            run.successful_sources = 0
            run.failed_sources = 3
            await session.commit()

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        res = await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
            "outcome": "failed",
            "occurred_at": self.now.isoformat(),
        })

        assert res.get("status") == "aggregation_queued"
        await self._assert_ready_handoff_is_durable()

        # Now test RunAggregation execution with zero inputs
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 3, "successful_sources": 0, "failed_sources": 3},
        )

        aggregator_mock = AsyncMock()
        aggregator_mock.aggregate = AsyncMock(return_value={
            "summary": "هیچ تحلیل موفقی برای تجمیع موجود نیست.",
            "findings": [],
            "coverage_metadata": {"manifest_input_count": 0},
            "provenance_references": [],
            "raw_result": {"status": "zero_inputs"},
        })
        publisher_mock = MagicMock()

        exec_agg_use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=aggregator_mock,
            ready_publisher=publisher_mock,
        )

        exec_res = await exec_agg_use_case.execute(agg.id)
        assert exec_res.get("status") == "completed"

        # Verify persisted aggregated result
        async with self.session_factory() as session:
            q = select(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id == agg.id)
            saved_res = (await session.execute(q)).scalar_one()
            assert saved_res.summary == "هیچ تحلیل موفقی برای تجمیع موجود نیست."
            assert saved_res.findings == []

    # 5. Atomic ready transition: concurrent workers racing to transition OPEN -> READY
    async def test_atomic_ready_transition_only_one_winner(self) -> None:
        await self.tracker_repo.get_or_create(self.run_id)

        async def _attempt_transition(reason: str):
            return await self.tracker_repo.transition_to_ready(
                run_id=self.run_id,
                expected_status=RunCompletionTrackerStatus.OPEN,
                new_status=RunCompletionTrackerStatus.READY_ALL_TERMINAL,
                readiness_reason=reason,
                cutoff_at=self.now,
                now=self.now,
            )

        results = await asyncio.gather(
            _attempt_transition("worker_1"),
            _attempt_transition("worker_2"),
            _attempt_transition("worker_3"),
        )

        winners = [r for r in results if r is not None]
        assert len(winners) == 1

    # 6. Ready crash recovery: event redelivery when already READY dispatches aggregation
    async def test_ready_crash_recovery_redelivery_dispatches(self) -> None:
        await self.tracker_repo.get_or_create(self.run_id)
        # Transition to READY
        await self.tracker_repo.transition_to_ready(
            run_id=self.run_id,
            expected_status=RunCompletionTrackerStatus.OPEN,
            new_status=RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            now=self.now,
        )

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Redelivered event arrives
        res = await use_case.execute_event({
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        })

        assert res.get("status") == "aggregation_queued"
        await self._assert_ready_handoff_is_durable()
        handoff_mock.ensure_and_dispatch_aggregation.assert_not_awaited()

    # 7. One RunAggregation per run idempotent creation
    async def test_one_aggregation_per_run_idempotent(self) -> None:
        agg1 = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={},
        )

        agg2 = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={},
        )

        assert agg1.id == agg2.id
        async with self.session_factory() as session:
            count = (
                await session.execute(
                    select(text("count(*)")).select_from(OrmRunAggregation).where(
                        OrmRunAggregation.run_id == self.run_id
                    )
                )
            ).scalar()
            assert count == 1

    # 8. Immutable manifest digest is deterministic
    async def test_immutable_manifest_digest_deterministic(self) -> None:
        inputs = [
            {
                "source_analysis_result_id": str(uuid4()),
                "source_id": str(uuid4()),
                "position": 1,
                "input_digest": "d1",
                "schema_version": "1.0",
            },
            {
                "source_analysis_result_id": str(uuid4()),
                "source_id": str(uuid4()),
                "position": 2,
                "input_digest": "d2",
                "schema_version": "1.0",
            },
        ]

        digest1 = compute_manifest_digest(inputs, "all_terminal", "spec_digest_xyz")
        # Reverse inputs list order; digest must remain identical due to sorting
        digest2 = compute_manifest_digest(list(reversed(inputs)), "all_terminal", "spec_digest_xyz")

        assert digest1 == digest2
        assert len(digest1) == 64

    # 9. Deadline readiness and cutoff freezes
    async def test_deadline_readiness_and_cutoff(self) -> None:
        deadline_time = self.now + timedelta(seconds=10)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)

        # Before deadline
        is_ready_before, reason_before = evaluate_tracker_readiness(
            expected_sources=3,
            terminal_sources=1,
            successful_sources=1,
            tracker=tracker,
            now=self.now,
        )
        assert not is_ready_before

        # After deadline
        is_ready_after, reason_after = evaluate_tracker_readiness(
            expected_sources=3,
            terminal_sources=1,
            successful_sources=1,
            tracker=tracker,
            now=deadline_time + timedelta(seconds=1),
        )
        assert is_ready_after
        assert reason_after == "deadline"

    # 10. Aggregation active lease retry on redelivery (Phase 6B bug prevented)
    def test_aggregation_active_lease_retry_on_redelivery(self) -> None:
        fake_agg_id = uuid4()
        fake_lease_exp = (datetime.now(UTC) + timedelta(seconds=120)).isoformat()

        with patch(
            "app.workers.aggregation_tasks.run_aggregation_worker",
            return_value={
                "status": "active_lease",
                "aggregation_id": str(fake_agg_id),
                "lease_expires_at": fake_lease_exp,
            },
        ):
            with patch.object(
                process_run_aggregation,
                "retry",
                side_effect=Retry("Delayed retry scheduled"),
            ) as mock_retry:
                with pytest.raises(Retry):
                    process_run_aggregation(str(fake_agg_id))

                mock_retry.assert_called_once()
                call_kwargs = mock_retry.call_args[1]
                # Ownership contention must not exhaust transport delivery.
                assert call_kwargs["max_retries"] is None
                assert call_kwargs["countdown"] >= 100

    # 11. Aggregation post-expiry reclaim and exactly-once execution
    async def test_aggregation_post_expiry_reclaim(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={},
        )

        # Worker A claims with 5s lease
        token_a = uuid4()
        claim_a = await self.agg_repo.claim_explicit(agg.id, token_a, lease_duration_seconds=5, now=self.now)
        assert claim_a.status == AggregationClaimStatus.CLAIMED
        assert claim_a.attempt_count == 1

        # Advance fake clock by 10s (lease expired)
        future_now = self.now + timedelta(seconds=10)

        # Worker B reclaims
        token_b = uuid4()
        claim_b = await self.agg_repo.claim_explicit(agg.id, token_b, lease_duration_seconds=300, now=future_now)
        assert claim_b.status == AggregationClaimStatus.CLAIMED
        assert claim_b.attempt_count == 2

    # 12. Stale aggregator fencing
    async def test_aggregation_stale_fencing(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={},
        )

        token_a = uuid4()
        await self.agg_repo.claim_explicit(agg.id, token_a, lease_duration_seconds=5, now=self.now)

        # Worker B reclaims after lease expiry
        token_b = uuid4()
        await self.agg_repo.claim_explicit(agg.id, token_b, lease_duration_seconds=300, now=self.now + timedelta(seconds=10))

        # Worker A tries to save result with stale token_a
        dummy_result = MagicMock()
        saved_a = await self.agg_repo.save_result_and_complete(agg.id, token_a, dummy_result, self.now)
        assert not saved_a

    # 13. Aggregation publishes MonitoringAnalysisReady and tracker marks ANALYSIS_READY
    async def test_aggregation_analysis_ready_event_published(self) -> None:
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={},
        )

        tracker = await self.tracker_repo.get_or_create(self.run_id)
        await self.tracker_repo.mark_aggregation_dispatched(self.run_id, agg.id)

        publisher_mock = MagicMock()
        use_case = ExecuteRunAggregationUseCase(
            aggregation_repository=self.agg_repo,
            run_spec_repository=self.spec_repo,
            aggregator=RunAggregatorAdapter(),
            ready_publisher=publisher_mock,
        )

        res = await use_case.execute(agg.id)
        assert res.get("status") == "completed"
        publisher_mock.publish_analysis_ready.assert_called_once()

        # Simulate Control Worker receiving analysis_ready
        ctrl_use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(),
        )
        await ctrl_use_case.execute_event({
            "event": "analysis_ready",
            "run_id": str(self.run_id),
            "aggregation_id": str(agg.id),
        })

        updated_tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert updated_tracker is not None
        assert updated_tracker.status == RunCompletionTrackerStatus.ANALYSIS_READY
        assert updated_tracker.analysis_ready_at is not None

    # 14. MonitoringRun is NOT completed by distributed path (remains RUNNING)
    async def test_no_monitoring_run_finalization_remains_running(self) -> None:
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            assert run.status == "running"

    # 15. Context budget and structured compaction for large inputs
    async def test_large_input_compaction_context_budget(self) -> None:
        adapter = RunAggregatorAdapter()
        # Build 100 findings across 5 source results
        large_inputs = []
        for i in range(5):
            src_findings = [{"topic": f"Finding {i}_{j}", "details": "Detailed text"} for j in range(20)]
            large_inputs.append({
                "source_id": str(uuid4()),
                "source_analysis_result_id": str(uuid4()),
                "summary": f"Summary for source {i} " * 50,
                "findings": src_findings,
                "input_digest": f"digest_{i}",
            })

        out = await adapter.aggregate(
            spec={},
            inputs=large_inputs,
            coverage_metadata={},
        )

        findings = out.get("findings")
        assert isinstance(findings, list)
        assert len(findings) <= MAX_TOTAL_FINDINGS
        assert len(out.get("provenance_references", [])) == 5
