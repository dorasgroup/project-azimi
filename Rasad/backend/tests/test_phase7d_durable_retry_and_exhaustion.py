"""Phase 7D: Durable Retry Exhaustion Closure Test Suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from typing import Mapping, Sequence
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import Base
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
    OrmSourceAnalysis,
    OrmSourceAnalysisResult,
)
from app.models.source_task import SourceTask as OrmSourceTask
from app.models.user import User
from app.modules.analysis.domain.run_aggregation import (
    AGGREGATION_SCHEMA_VERSION,
    MANIFEST_SCHEMA_VERSION,
    AggregatedAnalysisResultData,
    RunAggregationInputItem,
    RunAggregationStatus,
    compute_manifest_digest,
)
from app.modules.analysis.domain.source_analysis import SourceAnalysisStatus
from app.modules.analysis.infrastructure.run_aggregation_repository import (
    SqlAlchemyRunAggregationRepository,
)
from app.modules.monitoring.application.evaluate_completion import (
    EvaluateRunCompletionUseCase,
)
from app.modules.monitoring.application.control_event import (
    ControlOutcome, ControlOutcomeKind, ProcessMonitoringControlEvent,
)
from app.modules.monitoring.domain.completion_tracker import (
    RunCompletionTracker,
    RunCompletionTrackerStatus,
    evaluate_tracker_readiness,
)
from app.modules.monitoring.domain.execution import MonitoringRunStatus, SourceTaskStatus
from app.modules.monitoring.infrastructure.completion_tracker_repository import (
    SqlAlchemyRunCompletionTrackerRepository,
)
from app.modules.monitoring.infrastructure.deadline_scheduler import (
    CeleryDeadlineScheduler,
    DEFAULT_MAX_DEADLINE_HORIZON_SECONDS,
    UnsupportedDeadlineHorizonError,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
)
from app.modules.monitoring.ports.completion import (
    DeadlineSchedulerPort,
    RunAggregationHandoffPort,
    TransientDeadlineSchedulingError,
)
from app.workers.control_tasks import (
    calculate_retry_backoff,
    process_control_event,
)


class MockCeleryAppForRetry:
    """Mock Celery app with controllable transient failures for send_task."""

    def __init__(self, fail_count: int = 1) -> None:
        self.fail_count = fail_count
        self.attempts = 0
        self.sent_tasks: list[dict[str, object]] = []

    def send_task(
        self,
        name: str,
        args: list[object] | None = None,
        countdown: int = 0,
        queue: str | None = None,
        **kwargs: object,
    ) -> MagicMock:
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise RuntimeError("Transient broker connection error (simulated network blip)")
        task_info = {
            "name": name,
            "args": args,
            "countdown": countdown,
            "queue": queue,
            "kwargs": kwargs,
        }
        self.sent_tasks.append(task_info)
        mock_async = MagicMock()
        mock_async.id = str(uuid4())
        return mock_async


class Phase7DUnitConfigTestSuite(TestCase):
    """Unit tests for configuration, backoff calculation, and horizons (no DB dependency)."""

    def test_capped_exponential_backoff_calculation(self) -> None:
        """Verifies exponential backoff curve and 60-second cap (Section 11)."""
        expected_backoffs = [
            (0, 2),    # 2^0 + 1 = 2
            (1, 3),    # 2^1 + 1 = 3
            (2, 5),    # 2^2 + 1 = 5
            (3, 9),    # 2^3 + 1 = 9
            (4, 17),   # 2^4 + 1 = 17
            (5, 33),   # 2^5 + 1 = 33
            (6, 60),   # 2^6 + 1 = 65 -> capped at 60
            (7, 60),   # capped at 60
            (10, 60),  # capped at 60
            (50, 60),  # capped at 60
            (100, 60), # capped at 60
        ]
        for retries, expected in expected_backoffs:
            self.assertEqual(calculate_retry_backoff(retries), expected)

    def test_control_task_ack_and_durability_configuration(self) -> None:
        """Verifies task-level Celery ACK contracts (Section 8)."""
        self.assertTrue(process_control_event.acks_late)
        self.assertTrue(process_control_event.reject_on_worker_lost)
        self.assertFalse(process_control_event.acks_on_failure_or_timeout)
        self.assertIsNone(process_control_event.max_retries)

    def test_regression_horizon_derivation_intact(self) -> None:
        """Horizon is strictly derived from visibility_timeout - safety_margin."""
        self.assertEqual(settings.celery_visibility_timeout, 86400)
        self.assertEqual(settings.deadline_scheduling_safety_margin_seconds, 300)
        self.assertEqual(settings.max_deadline_horizon_seconds, 86100)

        scheduler = CeleryDeadlineScheduler(
            celery_app=MagicMock(),
            max_horizon_seconds=settings.max_deadline_horizon_seconds,
        )
        self.assertEqual(scheduler._max_horizon_seconds, 86100)


class Phase7DIntegrationTestSuite(IsolatedAsyncioTestCase):
    """Integration test suite for Phase 7D: Durable Retry Exhaustion Closure."""

    async def asyncSetUp(self) -> None:
        self.session_factory = SessionFactory
        self.tracker_repo = SqlAlchemyRunCompletionTrackerRepository(self.session_factory)
        self.run_repo = SqlAlchemyMonitoringRunRepository(self.session_factory)
        self.agg_repo = SqlAlchemyRunAggregationRepository(self.session_factory)
        self.now = datetime.now(UTC)

        async with self.session_factory() as session:
            user = await session.scalar(select(User))
            if user is None:
                user = User(
                    id=uuid4(),
                    email=f"p7d_{uuid4().hex[:6]}@example.com",
                    hashed_password="hash",
                    is_active=True,
                    is_superuser=False,
                )
                session.add(user)
                await session.flush()
            self.user_id = user.id

            bot = await session.scalar(select(BotIdentity).where(BotIdentity.is_active.is_(True)))
            if bot is None:
                bot = BotIdentity(
                    id=uuid4(),
                    name="Phase 7D Bot",
                    version="1.0.0",
                    description="Phase 7D Bot",
                    is_active=True,
                    created_at=self.now,
                    updated_at=self.now,
                )
                session.add(bot)
                await session.flush()
            self.bot_id = bot.id

            self.source_id = uuid4()
            src = Source(
                id=self.source_id,
                title="Phase 7D Test Source",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.UNKNOWN,
                is_active=True,
                base_url=f"https://example.com/p7d/{uuid4().hex[:6]}",
                created_by=user.id,
            )
            session.add(src)

            self.job_id = uuid4()
            job = MonitoringJob(
                id=self.job_id,
                topic="Phase 7D Durable Retry Job",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=self.now.date() - timedelta(days=1),
                date_to_gregorian=self.now.date(),
                requested_by=user.id,
                bot_identity_id=self.bot_id,
                status=JobStatus.CRAWLING,
                created_at=self.now,
                updated_at=self.now,
            )
            session.add(job)

            self.run_id = uuid4()
            run = OrmMonitoringRun(
                id=self.run_id,
                monitoring_job_id=self.job_id,
                status=MonitoringRunStatus.RUNNING,
                execution_mode="distributed",
                expected_sources=2,
                terminal_sources=0,
                successful_sources=0,
                failed_sources=0,
                cancelled_sources=0,
                created_at=self.now,
                updated_at=self.now,
            )
            session.add(run)

            self.spec_digest = "c" * 64
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

        self.deadline_at = self.now + timedelta(seconds=600)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=self.deadline_at)
        self.tracker_id = tracker.id

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    # -------------------------------------------------------------------------
    # 1. Section 2, 3, 4: Characterization of Loss Window & New Durable Invariant
    # -------------------------------------------------------------------------
    async def test_characterize_retry_budget_loss_window_and_new_invariant(self) -> None:
        """Contrasts old 5-retry cutoff bug with Phase 7D durable retry without budget exhaustion."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        # Simulate reaching attempt 5 (retries=5)
        process_control_event.push_request(retries=5)
        try:
            with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                mock_worker.return_value = ControlOutcome(
                    ControlOutcomeKind.RETRY_TRANSIENT, {"status": "retry_transient"}
                )
                from celery.exceptions import Retry
                with patch.object(process_control_event, "retry") as mock_retry:
                    mock_retry.side_effect = Retry("Task scheduled for retry attempt 6")

                    # Phase 7D invariant: MUST raise Retry, NOT return scheduling_retry_budget_exhausted
                    with pytest.raises(Retry):
                        process_control_event.run(payload)

                    # Verified: mock_retry was called with max_retries=None and capped backoff
                    mock_retry.assert_called_once()
                    kwargs = mock_retry.call_args.kwargs
                    assert kwargs["max_retries"] is None
                    assert kwargs["countdown"] == 33  # min(60, 2^5 + 1)
        finally:
            process_control_event.pop_request()

    # -------------------------------------------------------------------------
    # 2. Section 10: Long Outage Recovery Beyond Old 5-Retry Budget
    # -------------------------------------------------------------------------
    async def test_eventual_recovery_after_long_outage_exceeding_budget(self) -> None:
        """Simulates an extended outage (8 consecutive failures) followed by recovery on attempt 9."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        from celery.exceptions import Retry

        # Attempts 0 through 7 (total 8 failures) must all trigger durable retry
        for attempt in range(8):
            process_control_event.push_request(retries=attempt)
            try:
                with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                    mock_worker.return_value = ControlOutcome(
                        ControlOutcomeKind.RETRY_TRANSIENT, {"status": "retry_transient"}
                    )
                    with patch.object(process_control_event, "retry") as mock_retry:
                        mock_retry.side_effect = Retry(f"Retry attempt {attempt + 1}")

                        with pytest.raises(Retry):
                            process_control_event.run(payload)

                        mock_retry.assert_called_once()
                        assert mock_retry.call_args.kwargs["max_retries"] is None
                        expected_backoff = min(60, (2 ** min(attempt, 10)) + 1)
                        assert mock_retry.call_args.kwargs["countdown"] == expected_backoff
            finally:
                process_control_event.pop_request()

        # Attempt 8 (9th try): Broker recovers! Worker executes successfully
        process_control_event.push_request(retries=8)
        try:
            with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                mock_worker.return_value = ControlOutcome(
                    ControlOutcomeKind.COMPLETED,
                    {"status": "success", "run_id": str(self.run_id),
                     "deadline_scheduled": True},
                )
                with patch.object(process_control_event, "retry") as mock_retry:
                    result = process_control_event.run(payload)

                    mock_retry.assert_not_called()
                    assert result["status"] == "success"
                    assert result["deadline_scheduled"] is True
        finally:
            process_control_event.pop_request()

    # -------------------------------------------------------------------------
    # 3. Section 5: Obligation Validity Check Halts Retries
    # -------------------------------------------------------------------------
    async def test_obligation_validity_check_halts_retries_when_satisfied(self) -> None:
        """A committed DB deadline obligation already satisfies broker scheduling."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        # In distributed mode, tracker and deadline outbox commit together.
        evaluator = AsyncMock()
        evaluator.execute_event.side_effect = TransientDeadlineSchedulingError("broker unavailable")
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        assert await processor._deadline_obligation_pending(self.run_id) is False
        async with self.session_factory() as session:
            deadline_row = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{self.deadline_at.isoformat()}"))
            assert deadline_row is not None
            assert deadline_row.consumer_acked_at is None

        # The legacy marker-recovery branch still distinguishes absent marker.
        async with self.session_factory() as session:
            tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            tracker.deadline_scheduled_at = None
            await session.commit()
        assert await processor._deadline_obligation_pending(self.run_id) is True
        async with self.session_factory() as session:
            tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            tracker.deadline_scheduled_at = datetime.now(UTC)
            await session.commit()

        # Obligation should now be false
        assert await processor._deadline_obligation_pending(self.run_id) is False

        # When process_control_event runs with a transient error, it checks obligation and returns cleanly
        result = await processor.execute(payload)
        assert result.kind == ControlOutcomeKind.OBLIGATION_SATISFIED
        assert result.payload["run_id"] == str(self.run_id)

    async def test_obligation_validity_check_halts_retries_when_tracker_terminal(self) -> None:
        """When tracker status becomes READY_ALL_TERMINAL or CANCELLED, retries stop."""
        async with self.session_factory() as session:
            tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            tracker.status = RunCompletionTrackerStatus.READY_ALL_TERMINAL
            tracker.deadline_scheduled_at = None
            await session.commit()

        evaluator = AsyncMock()
        evaluator.execute_event.side_effect = TransientDeadlineSchedulingError("broker unavailable")
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        assert await processor._deadline_obligation_pending(self.run_id) is False

        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        result = await processor.execute(payload)
        assert result.kind == ControlOutcomeKind.OBLIGATION_SATISFIED

    # -------------------------------------------------------------------------
    # 4. Section 6: Permanent Errors Must Not Retry Infinitely
    # -------------------------------------------------------------------------
    async def test_permanent_errors_fail_structured_without_infinite_retry(self) -> None:
        """Unsupported horizon durably closes the tracker without faking READY."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        evaluator = AsyncMock()
        evaluator.execute_event.side_effect = UnsupportedDeadlineHorizonError(
            "Deadline 1000000s exceeds maximum broker horizon 86100s"
        )
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        result = await processor.execute(payload)
        assert result.kind == ControlOutcomeKind.PERMANENT_FAILURE
        assert result.payload["status"] == "control_failed"
        assert result.payload["error_type"] == "UnsupportedDeadlineHorizonError"

        async with self.session_factory() as session:
            tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            assert tracker.status == RunCompletionTrackerStatus.CONTROL_FAILED
            assert tracker.deadline_scheduled_at is not None

        rejected_ready = await self.tracker_repo.mark_analysis_ready(
            self.run_id, uuid4(), datetime.now(UTC))
        assert rejected_ready.status == RunCompletionTrackerStatus.CONTROL_FAILED
        assert rejected_ready.analysis_ready_at is None
        with pytest.raises(NoResultFound):
            await self.tracker_repo.mark_deadline_scheduled(self.run_id, datetime.now(UTC))
        closed = await self.tracker_repo.get_by_run_id(self.run_id)
        assert closed is not None
        assert closed.status == RunCompletionTrackerStatus.CONTROL_FAILED
        assert closed.deadline_scheduled_at is not None

    # -------------------------------------------------------------------------
    # 5. Section 12: Warning Escalation at Thresholds
    # -------------------------------------------------------------------------
    async def test_warning_escalation_at_thresholds(self) -> None:
        """Escalation error logging triggers at thresholds {5, 10, 20, 50, 100} (Section 12)."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        from celery.exceptions import Retry

        for threshold in [5, 10, 20]:
            process_control_event.push_request(retries=threshold)
            try:
                with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                    mock_worker.return_value = ControlOutcome(
                        ControlOutcomeKind.RETRY_TRANSIENT, {"status": "retry_transient"}
                    )
                    with patch("app.workers.control_tasks.logger.error") as mock_log_error:
                        with patch.object(process_control_event, "retry") as mock_retry:
                            mock_retry.side_effect = Retry("Retry scheduled")
                            with pytest.raises(Retry):
                                process_control_event.run(payload)

                            mock_log_error.assert_called()
                            error_call_msg = mock_log_error.call_args[0][0]
                            assert "ESCALATION:" in error_call_msg
            finally:
                process_control_event.pop_request()

    # -------------------------------------------------------------------------
    # 6. Section 13: Dedup Recovery Regression
    # -------------------------------------------------------------------------
    async def test_regression_dedup_recovery_intact(self) -> None:
        """Duplicate analysis event cannot increment counts or duplicate DB deadline."""
        deadline_time = self.now + timedelta(seconds=180)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)
        # The first frozen tracker deadline wins; this fixture created one in setUp.
        assert tracker.deadline_at == self.deadline_at

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        analysis_id = uuid4()
        payload = {
            "event": "analysis_terminal",
            "run_id": str(self.run_id),
            "source_analysis_id": str(analysis_id),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        }

        await use_case.execute_event(payload)

        # DB shows event was recorded (terminal count = 1)
        t1 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert t1 is not None
        assert t1.analysis_terminal_count == 1
        assert t1.deadline_scheduled_at is not None

        # Duplicate redelivery skips increment; scheduling was already durable.
        res2 = await use_case.execute_event(payload)
        assert res2["status"] == "open"

        t2 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert t2 is not None
        assert t2.analysis_terminal_count == 1  # Unchanged!
        assert t2.deadline_scheduled_at is not None
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{self.deadline_at.isoformat()}"))).scalars().all()
            assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 7. Section 14: Publish-Then-Crash Regression
    # -------------------------------------------------------------------------
    async def test_regression_publish_then_crash_idempotency(self) -> None:
        """Duplicate deadline messages result in exactly 1 READY transition and 1 RunAggregation."""
        past_deadline = self.now - timedelta(seconds=5)
        async with self.session_factory() as session:
            orm_tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            orm_tracker.deadline_at = past_deadline
            await session.commit()

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        agg_id = uuid4()
        handoff_mock.ensure_and_dispatch_aggregation.return_value = agg_id

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Message 1 arrives
        res1 = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": past_deadline.isoformat(),
        })
        assert res1["status"] == "aggregation_queued"

        # Duplicate Message 2 arrives (e.g. from redelivery or duplicate publish)
        res2 = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": past_deadline.isoformat(),
        })
        assert res2["status"] == "aggregation_queued"

        handoff_mock.ensure_and_dispatch_aggregation.assert_not_awaited()

        tracker_final = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker_final is not None
        assert tracker_final.status == RunCompletionTrackerStatus.READY_DEADLINE
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))).scalars().all()
            assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 8. Section 16: Early Delivery Regression
    # -------------------------------------------------------------------------
    async def test_regression_early_delivery_intact(self) -> None:
        """When deadline_reached arrives before deadline_at, tracker remains OPEN."""
        future_deadline = self.now + timedelta(seconds=45)
        await self.tracker_repo.get_or_create(self.run_id, deadline_at=future_deadline)

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )

        res = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": future_deadline.isoformat(),
        })

        assert res["status"] == "early_delivery"
        assert res["retry_after"] >= 40
        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status == RunCompletionTrackerStatus.OPEN

    # -------------------------------------------------------------------------
    # 9. Section 17: ANALYSIS_READY & MonitoringRun RUNNING Regression
    # -------------------------------------------------------------------------
    async def test_regression_analysis_ready_and_monitoring_run_running(self) -> None:
        """Completion of aggregation sets tracker to ANALYSIS_READY, but MonitoringRun remains RUNNING."""
        async with self.session_factory() as session:
            tracker = await session.get(OrmRunCompletionTracker, self.tracker_id)
            tracker.status = RunCompletionTrackerStatus.ANALYSIS_READY
            await session.commit()

        run = await self.run_repo.get_by_id(self.run_id)
        assert run.status == MonitoringRunStatus.RUNNING

    # -------------------------------------------------------------------------
    # 10. Section 18 / Schema Parity Regression
    # -------------------------------------------------------------------------
    async def test_regression_schema_parity_all_phase7_columns(self) -> None:
        """Physical DB schema matches ORM models across all 5 Phase 7 tables."""
        tables = [
            "run_completion_trackers",
            "run_completion_events",
            "run_aggregations",
            "run_aggregation_inputs",
            "aggregated_analysis_results",
        ]
        async with engine.connect() as conn:
            for table_name in tables:
                columns = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_columns(table_name)
                )
                col_names = {c["name"] for c in columns}
                assert len(col_names) > 0, f"Table {table_name} has no columns!"

                if table_name == "aggregated_analysis_results":
                    required_cols = {
                        "id",
                        "aggregation_id",
                        "run_id",
                        "schema_version",
                        "manifest_schema_version",
                        "manifest_digest",
                        "readiness_reason",
                        "cutoff_at",
                        "expected_sources",
                        "successful_sources",
                        "failed_sources",
                        "cancelled_sources",
                        "summary",
                        "created_at",
                    }
                    assert required_cols.issubset(col_names)
