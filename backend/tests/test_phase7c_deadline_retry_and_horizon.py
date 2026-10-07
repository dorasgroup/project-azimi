"""Phase 7C: Guaranteed Deadline Retry & Broker-Safe Horizon Test Suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings
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
from app.workers.control_tasks import process_control_event
from app.modules.monitoring.application.control_event import ControlOutcome, ControlOutcomeKind
from app.shared.integration.relay import OutboxRelay


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


class Phase7CTestSuite(IsolatedAsyncioTestCase):
    """Rigorous verification suite for Phase 7C: Guaranteed Deadline Retry & Horizon."""

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
                    email=f"p7c_{uuid4().hex[:6]}@example.com",
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
                    name="Phase 7C Bot",
                    version="1.0.0",
                    description="Phase 7C Bot",
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
                title="Phase 7C Test Source",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.UNKNOWN,
                is_active=True,
                base_url=f"https://example.com/p7c/{uuid4().hex[:6]}",
                created_by=user.id,
            )
            session.add(src)

            self.job_id = uuid4()
            job = MonitoringJob(
                id=self.job_id,
                topic="Phase 7C Guaranteed Deadline Job",
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

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    # -------------------------------------------------------------------------
    # 1. Section 6: Last Event + Broker Failure -> Control Task Self-Retry
    # -------------------------------------------------------------------------
    async def test_last_event_broker_failure_recovers_via_control_retry(self) -> None:
        """A broker outage leaves the committed deadline row for relay retry."""
        deadline_time = self.now + timedelta(seconds=120)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)
        assert tracker.deadline_scheduled_at is not None
        assert tracker.status == RunCompletionTrackerStatus.OPEN

        mock_celery = MockCeleryAppForRetry(fail_count=1)
        async with self.session_factory() as session:
            deadline_row = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{deadline_time.isoformat()}"))
            assert deadline_row is not None
            assert deadline_row.available_at == deadline_time

        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )

        # Terminal event arrives (only 1 event)
        terminal_task_id = uuid4()
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(terminal_task_id),
            "outcome": "completed",
            "occurred_at": self.now.isoformat(),
        }

        # The business event is not coupled to broker health.
        first = await use_case.execute_event(payload)
        assert first["status"] == "open"
        with pytest.raises(RuntimeError, match="Transient broker connection error"):
            await OutboxRelay(publisher=mock_celery)._publish(deadline_row)
        tracker_after_fail = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker_after_fail is not None
        assert tracker_after_fail.deadline_scheduled_at is not None
        assert tracker_after_fail.status == RunCompletionTrackerStatus.OPEN

        # Relay retries the SAME durable row when broker recovers.
        await OutboxRelay(publisher=mock_celery)._publish(deadline_row)
        assert len(mock_celery.sent_tasks) == 1
        assert mock_celery.sent_tasks[0]["name"] == "monitoring.process_control_event"
        assert mock_celery.sent_tasks[0]["queue"] == "monitoring.control"
        assert mock_celery.sent_tasks[0]["args"][0]["event"] == "deadline_reached"

    # -------------------------------------------------------------------------
    # 2. Section 7: Deduplicated Redelivery Recovery
    # -------------------------------------------------------------------------
    async def test_deduplicated_redelivery_does_not_increment_counts_but_recovers_deadline(self) -> None:
        """Duplicate analysis event cannot increment counts or duplicate deadline intent."""
        deadline_time = self.now + timedelta(seconds=180)
        await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)

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

        # Check DB counts after attempt 1
        t1 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert t1 is not None
        assert t1.analysis_terminal_count == 1
        assert t1.analysis_successful_count == 1
        assert t1.deadline_scheduled_at is not None

        # Attempt 2: Duplicate redelivery of the same analysis_terminal event
        await use_case.execute_event(payload)

        # Check DB counts after attempt 2: COUNTERS MUST NOT BE INCREMENTED AGAIN!
        t2 = await self.tracker_repo.get_by_run_id(self.run_id)
        assert t2 is not None
        assert t2.analysis_terminal_count == 1  # Still 1, NOT 2!
        assert t2.analysis_successful_count == 1  # Still 1, NOT 2!
        # The durable deadline obligation remains exactly one row.
        assert t2.deadline_scheduled_at is not None
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{deadline_time.isoformat()}"))).scalars().all()
            assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 3. Section 8: Retry Policy & Durable Retry Semantics (Phase 7C/7D)
    # -------------------------------------------------------------------------
    async def test_control_task_retries_beyond_old_budget_5(self) -> None:
        """Transient errors continue to retry past attempt 5 without exhausting to success."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        process_control_event.push_request(retries=5)
        try:
            with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                mock_worker.return_value = ControlOutcome(
                    ControlOutcomeKind.RETRY_TRANSIENT, {"status": "retry_transient"}
                )
                from celery.exceptions import Retry
                with patch.object(process_control_event, "retry") as mock_retry:
                    mock_retry.side_effect = Retry("Task scheduled for retry attempt 6")

                    with pytest.raises(Retry):
                        process_control_event.run(payload)

                    mock_retry.assert_called_once()
                    call_kwargs = mock_retry.call_args.kwargs
                    assert call_kwargs["max_retries"] is None
                    assert call_kwargs["countdown"] == 33  # min(60, 2^5 + 1)
        finally:
            process_control_event.pop_request()

    async def test_control_task_retries_with_exponential_backoff(self) -> None:
        """When transient error occurs, control task calls self.retry with backoff and max_retries=None."""
        payload = {
            "event": "source_terminal",
            "run_id": str(self.run_id),
            "source_task_id": str(uuid4()),
        }

        process_control_event.push_request(retries=2)
        try:
            with patch("app.workers.control_tasks.run_control_event_worker") as mock_worker:
                mock_worker.return_value = ControlOutcome(
                    ControlOutcomeKind.RETRY_TRANSIENT, {"status": "retry_transient"}
                )
                from celery.exceptions import Retry
                with patch.object(process_control_event, "retry") as mock_retry:
                    mock_retry.side_effect = Retry("Task scheduled for retry")

                    with pytest.raises(Retry):
                        process_control_event.run(payload)

                    # Backoff for retries=2: 2^2 + 1 = 5s
                    mock_retry.assert_called_once()
                    call_kwargs = mock_retry.call_args.kwargs
                    assert call_kwargs["countdown"] == 5
                    assert call_kwargs["max_retries"] is None
        finally:
            process_control_event.pop_request()

    # -------------------------------------------------------------------------
    # 4. Section 10, 11, 12: Broker-Safe Horizon Derived from Runtime Config
    # -------------------------------------------------------------------------
    async def test_derived_max_horizon_and_boundary_conditions(self) -> None:
        """Derived horizon obeys visibility_timeout - safety_margin with exact boundary semantics."""
        # 1. Verify default derivation: 86400 - 300 = 86100s
        assert settings.celery_visibility_timeout == 86400
        assert settings.deadline_scheduling_safety_margin_seconds == 300
        assert settings.max_deadline_horizon_seconds == 86100

        mock_celery = MagicMock()
        scheduler = CeleryDeadlineScheduler(celery_app=mock_celery)
        now = datetime.now(UTC)

        # Boundary A: delay < max_horizon (accepted)
        scheduler.schedule_deadline_signal(
            run_id=self.run_id,
            tracker_id=uuid4(),
            deadline_at=now + timedelta(seconds=86000),
        )
        assert mock_celery.send_task.call_count == 1

        # Boundary B: delay == max_horizon (accepted: delay <= max_horizon)
        scheduler.schedule_deadline_signal(
            run_id=self.run_id,
            tracker_id=uuid4(),
            deadline_at=now + timedelta(seconds=86100),
        )
        assert mock_celery.send_task.call_count == 2

        # Boundary C: delay > max_horizon (rejected: UnsupportedDeadlineHorizonError)
        with pytest.raises(UnsupportedDeadlineHorizonError) as exc_info:
            scheduler.schedule_deadline_signal(
                run_id=self.run_id,
                tracker_id=uuid4(),
                deadline_at=now + timedelta(seconds=86101),
            )
        assert "exceeds broker-safe horizon of 86100s" in str(exc_info.value)

        # Boundary D: Custom visibility timeout dynamically changes horizon (proving no hardcoded 86400)
        custom_settings = Settings(
            celery_visibility_timeout=3600,
            deadline_scheduling_safety_margin_seconds=300,
        )
        assert custom_settings.max_deadline_horizon_seconds == 3300

        custom_scheduler = CeleryDeadlineScheduler(
            celery_app=mock_celery,
            max_horizon_seconds=custom_settings.max_deadline_horizon_seconds,
        )

        # 3200s is safe within 3300s horizon
        custom_scheduler.schedule_deadline_signal(
            run_id=self.run_id,
            tracker_id=uuid4(),
            deadline_at=now + timedelta(seconds=3200),
        )
        assert mock_celery.send_task.call_count == 3

        # 3400s exceeds 3300s horizon
        with pytest.raises(UnsupportedDeadlineHorizonError) as exc_info2:
            custom_scheduler.schedule_deadline_signal(
                run_id=self.run_id,
                tracker_id=uuid4(),
                deadline_at=now + timedelta(seconds=3400),
            )
        assert "exceeds broker-safe horizon of 3300s" in str(exc_info2.value)

    # -------------------------------------------------------------------------
    # 5. Section 13: Early Delivery Regression
    # -------------------------------------------------------------------------
    async def test_early_delivery_leaves_tracker_open_and_returns_retry_after(self) -> None:
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
    # 6. Section 14: Publish-Then-Crash Duplicate Safety Regression
    # -------------------------------------------------------------------------
    async def test_publish_then_crash_duplicate_safety(self) -> None:
        """Duplicate deadline messages result in exactly 1 READY transition and 1 RunAggregation."""
        past_deadline = self.now - timedelta(seconds=5)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=past_deadline)

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
        assert tracker_final.readiness_reason == "deadline"
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))).scalars().all()
            assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 7. Section 15: Schema Parity Regression Across Tables
    # -------------------------------------------------------------------------
    async def test_schema_parity_regression(self) -> None:
        """Verify that all Phase 7/7A/7B tables maintain 100% schema parity."""
        target_tables = [
            "run_completion_trackers",
            "run_completion_events",
            "run_aggregations",
            "run_aggregation_inputs",
            "aggregated_analysis_results",
        ]

        async with engine.connect() as conn:
            def _check(connection: object) -> list[str]:
                insp = inspect(connection)
                mismatches = []
                for table_name in target_tables:
                    orm_table = Base.metadata.tables.get(table_name)
                    if orm_table is None:
                        mismatches.append(f"Table {table_name} missing from ORM")
                        continue
                    phys_cols = {c["name"]: c for c in insp.get_columns(table_name)}
                    orm_cols = {c.name: c for c in orm_table.columns}
                    for col_name in orm_cols:
                        if col_name not in phys_cols:
                            mismatches.append(f"{table_name}.{col_name} missing in physical DB")
                    for col_name in phys_cols:
                        if col_name not in orm_cols:
                            mismatches.append(f"{table_name}.{col_name} missing in ORM")
                return mismatches

            errs = await conn.run_sync(_check)
            assert len(errs) == 0, f"Schema mismatches detected: {errs}"

    # -------------------------------------------------------------------------
    # 8. Section 17: Phase 8 Consumer Contract Regression
    # -------------------------------------------------------------------------
    async def test_phase8_consumer_contract_regression(self) -> None:
        """Ensure AggregatedAnalysisResult satisfies full Phase 8 consumer contract."""
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="deadline",
            cutoff_at=self.now,
            completion_metadata={
                "expected_sources": 2,
                "successful_sources": 1,
                "failed_sources": 1,
                "cancelled_sources": 0,
                "analysis_successful": 1,
                "analysis_failed": 1,
                "analysis_cancelled": 0,
            },
        )
        claim_token = uuid4()
        claim = await self.agg_repo.claim_explicit(agg.id, claim_token, lease_duration_seconds=60, now=self.now)
        assert claim.status.value == "claimed"

        result_data = AggregatedAnalysisResultData(
            id=uuid4(),
            aggregation_id=agg.id,
            run_id=self.run_id,
            schema_version=AGGREGATION_SCHEMA_VERSION,
            manifest_digest=agg.manifest_digest,
            readiness_reason="deadline",
            expected_sources=2,
            successful_sources=1,
            failed_sources=1,
            cancelled_sources=0,
            analysis_successful=1,
            analysis_failed=1,
            analysis_cancelled=0,
            summary="Phase 7C Aggregated Summary",
            findings=[{"insight": "Phase 7C Hardening Verified"}],
            coverage_metadata={"coverage": "50%"},
            provenance_references=[],
            raw_result={"meta": "ok"},
            created_at=self.now,
            cutoff_at=self.now,
            manifest_schema_version=MANIFEST_SCHEMA_VERSION,
        )

        completed = await self.agg_repo.save_result_and_complete(
            agg.id, claim_token, result_data, self.now
        )
        assert completed is True

        async with self.session_factory() as session:
            orm_result = await session.scalar(
                select(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id == agg.id)
            )
            assert orm_result is not None
            assert orm_result.manifest_digest == agg.manifest_digest
            assert orm_result.readiness_reason == "deadline"
            assert orm_result.schema_version == AGGREGATION_SCHEMA_VERSION
            assert orm_result.manifest_schema_version == MANIFEST_SCHEMA_VERSION
            assert orm_result.summary == "Phase 7C Aggregated Summary"
            assert len(orm_result.findings) == 1
