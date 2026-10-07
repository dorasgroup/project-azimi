"""Phase 7B: 100% Schema Parity & Deadline Publisher Crash Window Hardening Test Suite."""

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
from app.modules.analysis.domain.source_analysis import SourceAnalysisStatus
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
from app.modules.monitoring.application.control_event import (
    ControlOutcomeKind, ProcessMonitoringControlEvent,
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
)
from app.shared.integration.outbox import enqueue
from app.shared.integration.relay import OutboxRelay


def consume_for_report(
    result: AggregatedAnalysisResultData,
    manifest_inputs: Sequence[dict[str, object]],
) -> dict[str, object]:
    """Phase 8 consumer reading exclusively from AggregatedAnalysisResult + manifest."""
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


class MockFailingScheduler(DeadlineSchedulerPort):
    def __init__(self, fail_first_n_times: int = 1) -> None:
        self.fail_times = fail_first_n_times
        self.call_count = 0
        self.scheduled: list[dict[str, object]] = []

    def schedule_deadline_signal(
        self,
        run_id: UUID,
        tracker_id: UUID,
        deadline_at: datetime,
    ) -> None:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            raise RuntimeError("Broker connection timeout")
        self.scheduled.append({
            "run_id": run_id,
            "tracker_id": tracker_id,
            "deadline_at": deadline_at,
        })


class MockCrashAfterPublishScheduler(DeadlineSchedulerPort):
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


@pytest.mark.asyncio
class TestPhase7BParityAndDeadlines(IsolatedAsyncioTestCase):
    """Rigorous test suite for Phase 7B 100% Schema Parity & Deadline Hardening."""

    async def asyncSetUp(self) -> None:
        self.session_factory = SessionFactory
        self.now = datetime.now(UTC)

        async with self.session_factory() as session:
            user = await session.scalar(select(User).limit(1))
            if not user:
                user = User(
                    id=uuid4(),
                    email=f"p7b-{uuid4().hex[:6]}@example.org",
                    password_hash="fake",
                    full_name="Phase 7B Tester",
                    is_active=True,
                )
                session.add(user)
                await session.flush()
            self.user_id = user.id

            bot = await session.scalar(select(BotIdentity).limit(1))
            if not bot:
                bot = BotIdentity(
                    id=uuid4(),
                    name="Phase 7B Bot",
                    version="1.0.0",
                    description="Phase 7B Bot",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(bot)
                await session.flush()
            self.bot_id = bot.id

            self.source_id = uuid4()
            src = Source(
                id=self.source_id,
                title="Phase 7B Test Source",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.UNKNOWN,
                is_active=True,
                base_url=f"https://example.com/p7b/{uuid4().hex[:6]}",
                created_by=user.id,
            )
            session.add(src)

            self.job_id = uuid4()
            job = MonitoringJob(
                id=self.job_id,
                topic="Phase 7B Fan-In Parity Job",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=self.now.date() - timedelta(days=1),
                date_to_gregorian=self.now.date(),
                requested_by=user.id,
                bot_identity_id=self.bot_id,
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
                expected_sources=2,
                terminal_sources=0,
                successful_sources=0,
                failed_sources=0,
                cancelled_sources=0,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(run)

            self.spec_digest = "b" * 64
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

    # 1. Gate 1: Automated Schema-Parity Integration Test (Section 9)
    async def test_orm_physical_schema_parity(self) -> None:
        """Automated schema-parity test: verifies 100% parity between ORM and physical DB."""
        target_tables = [
            "run_completion_trackers",
            "run_completion_events",
            "run_aggregations",
            "run_aggregation_inputs",
            "aggregated_analysis_results",
        ]

        async with engine.connect() as conn:
            def verify_parity(connection):
                insp = inspect(connection)
                for table_name in target_tables:
                    orm_table = Base.metadata.tables.get(table_name)
                    assert orm_table is not None, f"Table {table_name} missing in ORM metadata"

                    phys_cols = {c["name"]: c for c in insp.get_columns(table_name)}
                    orm_cols = {c.name: c for c in orm_table.columns}

                    # Assert every ORM column exists in physical schema with identical nullability
                    for col_name, orm_c in orm_cols.items():
                        assert col_name in phys_cols, f"{table_name}.{col_name} missing from physical schema!"
                        db_c = phys_cols[col_name]
                        assert orm_c.nullable == db_c["nullable"], (
                            f"{table_name}.{col_name} nullability mismatch: ORM={orm_c.nullable} vs DB={db_c['nullable']}"
                        )

                    # Assert no extra undeclared columns in physical schema
                    for col_name in phys_cols:
                        assert col_name in orm_cols, f"Physical DB has unexpected column {table_name}.{col_name}"

                # Specific contract checks:
                # A) run_completion_trackers.cutoff_at
                tracker_cols = {c["name"]: c for c in insp.get_columns("run_completion_trackers")}
                assert "cutoff_at" in tracker_cols
                assert tracker_cols["cutoff_at"]["nullable"] is True
                assert "deadline_scheduled_at" in tracker_cols
                assert tracker_cols["deadline_scheduled_at"]["nullable"] is True

                # B) aggregated_analysis_results cutoff_at & manifest_schema_version
                res_cols = {c["name"]: c for c in insp.get_columns("aggregated_analysis_results")}
                assert "cutoff_at" in res_cols
                assert res_cols["cutoff_at"]["nullable"] is True
                assert "manifest_schema_version" in res_cols
                assert res_cols["manifest_schema_version"]["nullable"] is False

                # C) run_aggregation_inputs foreign keys and constraints
                input_fks = insp.get_foreign_keys("run_aggregation_inputs")
                referred_tables = {fk["referred_table"] for fk in input_fks}
                assert "run_aggregations" in referred_tables
                assert "source_analysis_results" in referred_tables
                assert "sources" in referred_tables

                input_uqs = insp.get_unique_constraints("run_aggregation_inputs")
                uq_cols = [set(u["column_names"]) for u in input_uqs]
                assert {"aggregation_id", "source_analysis_result_id"} in uq_cols

            await conn.run_sync(verify_parity)

    # 2. Gate 1: Repository Smoke Against Migration-Built DB (Section 10)
    async def test_repository_smoke_against_migration_db(self) -> None:
        """Smoke test repository CRUD and lifecycle purely against Alembic-migrated DB."""
        # A) CompletionTracker create/read/update
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=self.now + timedelta(seconds=60))
        assert tracker is not None
        assert tracker.status == RunCompletionTrackerStatus.OPEN
        assert tracker.analysis_terminal_count == 0

        # Record event
        is_new, updated_tracker = await self.tracker_repo.record_event_and_update_counts(
            run_id=self.run_id,
            event_kind="source_terminal",
            entity_id=uuid4(),
            outcome="completed",
            occurred_at=self.now,
        )
        assert is_new is True

        # READY_ALL_TERMINAL sets cutoff_at
        ready_tracker = await self.tracker_repo.transition_to_ready(
            run_id=self.run_id,
            expected_status=RunCompletionTrackerStatus.OPEN,
            new_status=RunCompletionTrackerStatus.READY_ALL_TERMINAL,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            now=self.now,
        )
        assert ready_tracker is not None
        assert ready_tracker.cutoff_at == self.now
        assert ready_tracker.readiness_reason == "all_terminal"

    # 3. Gate 1: Manifest Contract Test on Physical Schema (Section 16)
    async def test_manifest_multi_input_contract_and_digest(self) -> None:
        """Test manifest creation with multiple inputs, ordering, digests, and constraints."""
        res_ids = []
        async with self.session_factory() as session:
            for i in range(3):
                src = Source(
                    id=uuid4(),
                    title=f"Source 7B {i}",
                    type=SourceType.NEWS_SITE,
                    last_status=SourceStatus.UNKNOWN,
                    is_active=True,
                    base_url=f"https://example.com/p7b/{i}/{uuid4().hex[:6]}",
                    created_by=self.user_id,
                )
                session.add(src)
                await session.flush()

                t = OrmSourceTask(
                    id=uuid4(),
                    run_id=self.run_id,
                    source_id=src.id,
                    ordinal=i + 1,
                    status=SourceTaskStatus.COMPLETED,
                    created_at=self.now,
                    updated_at=self.now,
                )
                session.add(t)
                await session.flush()

                sa = OrmSourceAnalysis(
                    id=uuid4(),
                    source_task_id=t.id,
                    run_id=self.run_id,
                    source_id=src.id,
                    status=SourceAnalysisStatus.COMPLETED,
                    created_at=self.now,
                    updated_at=self.now,
                )
                session.add(sa)
                await session.flush()

                r_id = uuid4()
                res_ids.append(r_id)
                res = OrmSourceAnalysisResult(
                    id=r_id,
                    source_analysis_id=sa.id,
                    source_task_id=t.id,
                    run_id=self.run_id,
                    source_id=src.id,
                    schema_version="1.0",
                    summary=f"Analysis summary {i}",
                    findings=[{"topic": f"topic_{i}"}],
                    evidence_map={},
                    input_digest=f"digest_inp_{i}_{uuid4().hex[:16]}",
                    created_at=self.now,
                )
                session.add(res)
            await session.commit()

        completion_meta = {
            "expected_sources": 3,
            "successful_sources": 3,
            "failed_sources": 0,
            "cancelled_sources": 0,
            "analysis_successful": 3,
            "analysis_failed": 0,
            "analysis_cancelled": 0,
        }

        # Ensure aggregation and manifest
        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata=completion_meta,
        )
        assert agg is not None

        # Verify inputs from physical table
        inputs = await self.agg_repo.load_manifest_inputs(agg.id)
        assert len(inputs) == 3
        # Deterministic position check
        positions = [item["position"] for item in inputs]
        assert positions == [1, 2, 3]

        # Verify manifest digest matches canonical compute_manifest_digest
        computed_digest = compute_manifest_digest(
            inputs=inputs,
            readiness_reason=agg.readiness_reason,
            spec_digest=agg.run_analysis_spec_digest,
            cutoff_at=agg.cutoff_at,
            completion_counts=completion_meta,
        )
        assert agg.manifest_digest == computed_digest

    # 4. Gate 1: Aggregated Result Physical Contract & Phase 8 Consumer (Section 15 & 17)
    async def test_aggregated_result_physical_contract_and_consumer(self) -> None:
        """Test physical round-trip persistence of AggregatedAnalysisResult and Phase 8 consumer."""
        res_id = uuid4()
        async with self.session_factory() as session:
            t = OrmSourceTask(
                id=uuid4(),
                run_id=self.run_id,
                source_id=self.source_id,
                ordinal=1,
                status=SourceTaskStatus.COMPLETED,
                created_at=self.now,
                updated_at=self.now,
            )
            session.add(t)
            await session.flush()

            sa = OrmSourceAnalysis(
                id=uuid4(),
                source_task_id=t.id,
                run_id=self.run_id,
                source_id=self.source_id,
                status=SourceAnalysisStatus.COMPLETED,
                created_at=self.now,
                updated_at=self.now,
            )
            session.add(sa)
            await session.flush()

            res = OrmSourceAnalysisResult(
                id=res_id,
                source_analysis_id=sa.id,
                source_task_id=t.id,
                run_id=self.run_id,
                source_id=self.source_id,
                schema_version="1.0",
                summary="Source summary 7B",
                findings=[{"sentiment": "positive", "trend": "rising"}],
                evidence_map={},
                input_digest="digest_test_7b_inp_01",
                created_at=self.now,
            )
            session.add(res)
            await session.commit()

        agg = await self.agg_repo.ensure_aggregation_and_manifest(
            run_id=self.run_id,
            readiness_reason="all_terminal",
            cutoff_at=self.now,
            completion_metadata={"expected_sources": 1, "successful_sources": 1},
        )

        claim_token = uuid4()
        claim = await self.agg_repo.claim_explicit(agg.id, claim_token, lease_duration_seconds=300, now=self.now)
        assert claim.status == AggregationClaimStatus.CLAIMED

        result_id = uuid4()
        result_data = AggregatedAnalysisResultData(
            id=result_id,
            aggregation_id=agg.id,
            run_id=self.run_id,
            schema_version="1.0",
            manifest_digest=agg.manifest_digest,
            readiness_reason="all_terminal",
            expected_sources=1,
            successful_sources=1,
            failed_sources=0,
            cancelled_sources=0,
            analysis_successful=1,
            analysis_failed=0,
            analysis_cancelled=0,
            summary="Synthesized Executive Summary for Phase 7B",
            findings=[{"topic": "AI", "sentiment": "positive"}],
            coverage_metadata={"expected_sources": 1, "successful_sources": 1},
            provenance_references=[{"source_id": str(self.source_id), "source_analysis_result_id": str(res_id)}],
            raw_result={"source_count": 1},
            created_at=self.now,
            cutoff_at=self.now,
            manifest_schema_version="2.0",
        )

        completed = await self.agg_repo.save_result_and_complete(agg.id, claim_token, result_data, self.now)
        assert completed is True

        # Query physical table directly
        async with self.session_factory() as session:
            orm_result = await session.scalar(
                select(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.aggregation_id == agg.id)
            )
            assert orm_result is not None
            assert orm_result.id == result_id
            assert orm_result.schema_version == "1.0"
            assert orm_result.manifest_schema_version == "2.0"
            assert orm_result.manifest_digest == agg.manifest_digest
            assert orm_result.readiness_reason == "all_terminal"
            assert orm_result.cutoff_at == self.now
            assert orm_result.summary == "Synthesized Executive Summary for Phase 7B"
            assert len(orm_result.findings) == 1
            assert orm_result.expected_sources == 1

        # Phase-8 Consumer test on Alembic-built DB
        manifest_inputs = await self.agg_repo.load_manifest_inputs(agg.id)
        report_data = consume_for_report(result_data, manifest_inputs)
        assert report_data["summary"] != ""
        assert len(report_data["findings"]) == 1
        assert report_data["manifest_schema_version"] == "2.0"

    # 5. Gate 2: Deadline Publish Failure Test (Section 20)
    async def test_deadline_publish_failure_leaves_marker_null(self) -> None:
        """Broker failure cannot erase the committed DB deadline obligation."""
        deadline_time = self.now + timedelta(seconds=120)
        tracker = await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)
        assert tracker.deadline_scheduled_at is not None
        async with self.session_factory() as session:
            row = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{deadline_time.isoformat()}"))
            assert row is not None
            assert row.available_at == deadline_time
            assert row.consumer_acked_at is None

        broken_broker = MagicMock()
        broken_broker.send_task.side_effect = RuntimeError("Broker connection timeout")
        with pytest.raises(RuntimeError, match="Broker connection timeout"):
            await OutboxRelay(publisher=broken_broker)._publish(row)
        persisted_tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert persisted_tracker is not None
        assert persisted_tracker.deadline_scheduled_at is not None
        assert persisted_tracker.status == RunCompletionTrackerStatus.OPEN
        recovered_broker = MagicMock()
        await OutboxRelay(publisher=recovered_broker)._publish(row)
        recovered_broker.send_task.assert_called_once()

    # 6. Gate 2: Deadline Publish-Then-Crash Test (Section 21)
    async def test_deadline_publish_then_crash_duplicate_safety(self) -> None:
        """Duplicate relay publication still produces one logical READY handoff."""
        deadline_time = self.now + timedelta(seconds=30)
        await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline_time)
        async with self.session_factory() as session:
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"deadline:{self.run_id}:{deadline_time.isoformat()}"))
            assert parent is not None
        publisher = MagicMock()
        relay = OutboxRelay(publisher=publisher)
        await relay._publish(parent)
        # Simulated crash before PUBLISHED marker: the same durable row is retried.
        await relay._publish(parent)
        assert publisher.send_task.call_count == 2
        handoff_mock = AsyncMock(spec=RunAggregationHandoffPort)
        handoff_mock.ensure_and_dispatch_aggregation.return_value = uuid4()

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=handoff_mock,
        )
        # Simulate arrival at deadline; duplicate deliveries are idempotent.
        past_deadline = self.now - timedelta(seconds=1)
        async with self.session_factory() as session:
            t = await session.scalar(
                select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == self.run_id)
            )
            t.deadline_at = past_deadline
            await session.commit()

        # Message 1 arrives at control worker.
        res1 = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": past_deadline.isoformat(),
        })
        assert res1["status"] == "aggregation_queued"
        handoff_mock.ensure_and_dispatch_aggregation.assert_not_awaited()

        # Message 2 arrives later (duplicate redelivery).
        res2 = await use_case.execute_event({
            "event": "deadline_reached",
            "run_id": str(self.run_id),
            "deadline_at": past_deadline.isoformat(),
        })
        assert res2["status"] == "aggregation_queued"
        async with self.session_factory() as session:
            rows = (await session.execute(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))).scalars().all()
            assert len(rows) == 1

    # 7. Gate 2: Long-Horizon Deadline Safety Test (Section 22 & 23)
    def test_long_horizon_deadline_safety(self) -> None:
        """CeleryDeadlineScheduler rejects horizons exceeding broker visibility timeout."""
        mock_celery = MagicMock()
        scheduler = CeleryDeadlineScheduler(
            celery_app=mock_celery,
            max_horizon_seconds=DEFAULT_MAX_DEADLINE_HORIZON_SECONDS,  # 86400s
        )

        now = datetime.now(UTC)
        far_future_deadline = now + timedelta(days=2)  # 172800s > 86400s

        with pytest.raises(UnsupportedDeadlineHorizonError) as exc_info:
            scheduler.schedule_deadline_signal(
                run_id=self.run_id,
                tracker_id=uuid4(),
                deadline_at=far_future_deadline,
            )

        assert "Deadline capability is supported only within configured broker-safe horizon." in str(exc_info.value)
        mock_celery.send_task.assert_not_called()

        # Within horizon: 12 hours (< 24h)
        safe_deadline = now + timedelta(hours=12)
        scheduler.schedule_deadline_signal(
            run_id=self.run_id,
            tracker_id=uuid4(),
            deadline_at=safe_deadline,
        )
        mock_celery.send_task.assert_called_once()

    # 8. Gate 2: ANALYSIS_READY Regression Test (Section 25)
    async def test_analysis_ready_leaves_monitoring_run_running(self) -> None:
        """When analysis_ready integration event arrives, MonitoringRun remains strictly RUNNING."""
        await self.tracker_repo.get_or_create(self.run_id)
        agg_id = uuid4()
        # analysis_ready is valid only for the aggregation previously accepted
        # by the tracker; an arbitrary event must not promote OPEN to READY.
        await self.tracker_repo.mark_aggregation_dispatched(self.run_id, agg_id)

        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )

        res = await use_case.execute_event({
            "event": "analysis_ready",
            "run_id": str(self.run_id),
            "aggregation_id": str(agg_id),
        })
        assert res["status"] == "analysis_ready"

        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status == RunCompletionTrackerStatus.ANALYSIS_READY
        assert tracker.analysis_ready_at is not None

        # Verify MonitoringRun in DB remains RUNNING (Phase 8 finalizes runs, Phase 7/7B strictly does not!)
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            assert run.status == MonitoringRunStatus.RUNNING

    async def test_analysis_ready_rejects_unmatched_predecessor(self) -> None:
        """A stale or out-of-order ready event cannot claim a tracker transition."""
        await self.tracker_repo.get_or_create(self.run_id)
        agg_id = uuid4()
        use_case = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo,
            run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )
        result = await use_case.execute_event({
            "event": "analysis_ready", "run_id": str(self.run_id),
            "aggregation_id": str(agg_id),
        })
        assert result["status"] == "analysis_ready_rejected"
        tracker = await self.tracker_repo.get_by_run_id(self.run_id)
        assert tracker is not None
        assert tracker.status == RunCompletionTrackerStatus.OPEN
        assert tracker.analysis_ready_at is None

    async def test_phase7h_control_ack_and_ready_handoff_are_atomic(self) -> None:
        """A crash before commit loses neither control input nor successor obligation."""
        await self.tracker_repo.get_or_create(self.run_id)
        analysis_id = uuid4()
        parent_key = f"analysis-terminal:{analysis_id}"
        successor_key = f"run-aggregation-handoff:{self.run_id}"
        payload = {
            "schema_version": 1, "event": "analysis_terminal",
            "run_id": str(self.run_id), "source_analysis_id": str(analysis_id),
            "outcome": "completed", "occurred_at": self.now.isoformat(),
        }
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.terminal_sources = 2
            run.successful_sources = 1
            await enqueue(session, message_key=parent_key,
                          message_type="ANALYSIS_TERMINAL", payload=payload)
            await session.commit()
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id

        with patch(
            "app.modules.monitoring.infrastructure.completion_tracker_repository.acknowledge_in_session",
            side_effect=RuntimeError("injected crash before commit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected crash"):
                await self.tracker_repo.apply_outbox_event(payload, parent_id, datetime.now(UTC))

        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            event_row = await session.scalar(select(OrmRunCompletionEvent).where(
                OrmRunCompletionEvent.entity_id == analysis_id))
            successor = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == successor_key))
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            assert parent is not None and tracker is not None
            assert parent.consumer_acked_at is None
            assert event_row is None
            assert successor is None
            assert tracker.status == RunCompletionTrackerStatus.OPEN.value
            assert tracker.analysis_terminal_count == 0

        result = await self.tracker_repo.apply_outbox_event(payload, parent_id, datetime.now(UTC))
        assert result["status"] == "aggregation_queued"
        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            successor = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == successor_key))
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            assert parent is not None and tracker is not None
            assert parent.consumer_acked_at is not None
            assert successor is not None
            assert tracker.status == RunCompletionTrackerStatus.READY_ALL_TERMINAL.value
            assert tracker.analysis_terminal_count == 1

    async def test_phase7h_permanent_control_failure_ack_is_atomic(self) -> None:
        """A malformed durable event closes the tracker and its delivery together."""
        payload = {"schema_version": 1, "event": "invalid_event", "run_id": str(self.run_id)}
        parent_key = f"phase7h-invalid-control:{uuid4()}"
        async with self.session_factory() as session:
            await enqueue(session, message_key=parent_key,
                          message_type="SOURCE_TERMINAL", payload=payload)
            await session.commit()
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id

        evaluator = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo, run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        outcome = await processor.execute(payload, outbox_id=parent_id)
        assert outcome.kind == ControlOutcomeKind.PERMANENT_FAILURE
        assert outcome.payload["status"] == "control_failed"
        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            assert parent is not None and tracker is not None
            assert parent.consumer_acked_at is not None
            assert tracker.status == RunCompletionTrackerStatus.CONTROL_FAILED.value

    async def test_phase7h_invalid_run_id_is_quarantined(self) -> None:
        payload = {"schema_version": 1, "event": "source_terminal", "run_id": "not-a-uuid",
                   "source_task_id": str(uuid4())}
        parent_key = f"phase7h-invalid-run:{uuid4()}"
        async with self.session_factory() as session:
            # New producers reject malformed identifiers before persistence.
            with self.assertRaises(ValueError):
                await enqueue(session, message_key=parent_key,
                              message_type="SOURCE_TERMINAL", payload=payload)
            assert await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key)) is None
            # Simulate a legacy/corrupt persisted row to retain the consumer's
            # quarantine proof rather than dropping that invariant.
            session.add(IntegrationOutbox(
                id=uuid4(), message_key=parent_key, message_type="SOURCE_TERMINAL",
                payload_json=payload, status="PENDING", available_at=datetime.now(UTC),
            ))
            await session.commit()
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id
        evaluator = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo, run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        outcome = await processor.execute(payload, outbox_id=parent_id)
        assert outcome.kind == ControlOutcomeKind.PERMANENT_FAILURE
        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            assert parent is not None
            assert parent.status == "FAILED"
            assert parent.last_error_code == "InvalidRunId"
            assert parent.consumer_acked_at is None

    async def test_phase7h_invalid_analysis_outcome_cannot_poison_counts(self) -> None:
        payload = {
            "schema_version": 1, "event": "analysis_terminal",
            "run_id": str(self.run_id), "source_analysis_id": str(uuid4()),
            "outcome": "unknown",
        }
        parent_key = f"phase7h-invalid-outcome:{uuid4()}"
        async with self.session_factory() as session:
            await enqueue(session, message_key=parent_key,
                          message_type="ANALYSIS_TERMINAL", payload=payload)
            await session.commit()
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id
        evaluator = EvaluateRunCompletionUseCase(
            tracker_repository=self.tracker_repo, run_repository=self.run_repo,
            aggregation_handoff=AsyncMock(spec=RunAggregationHandoffPort),
        )
        processor = ProcessMonitoringControlEvent(evaluator, self.tracker_repo, self.run_repo)
        outcome = await processor.execute(payload, outbox_id=parent_id)
        assert outcome.kind == ControlOutcomeKind.PERMANENT_FAILURE
        async with self.session_factory() as session:
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            parent = await session.get(IntegrationOutbox, parent_id)
            assert tracker is not None and parent is not None
            assert tracker.analysis_terminal_count == 0
            assert tracker.status == RunCompletionTrackerStatus.CONTROL_FAILED.value
            assert parent.consumer_acked_at is not None

    async def test_phase7h_source_terminal_ack_and_ready_handoff(self) -> None:
        await self.tracker_repo.get_or_create(self.run_id)
        source_task_id = uuid4()
        payload = {
            "schema_version": 1, "event": "source_terminal",
            "run_id": str(self.run_id), "source_task_id": str(source_task_id),
            "outcome": "failed", "occurred_at": self.now.isoformat(),
        }
        parent_key = f"source-terminal:{source_task_id}"
        async with self.session_factory() as session:
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert run is not None
            run.terminal_sources = 2
            run.failed_sources = 2
            await enqueue(session, message_key=parent_key,
                          message_type="SOURCE_TERMINAL", payload=payload)
            await session.commit()
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id

        result = await self.tracker_repo.apply_outbox_event(payload, parent_id, datetime.now(UTC))
        assert result["status"] == "aggregation_queued"
        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            event_row = await session.scalar(select(OrmRunCompletionEvent).where(
                OrmRunCompletionEvent.entity_id == source_task_id))
            successor = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))
            assert parent is not None and tracker is not None
            assert parent.consumer_acked_at is not None
            assert event_row is not None and successor is not None
            assert tracker.status == RunCompletionTrackerStatus.READY_ALL_TERMINAL.value

    async def test_phase7h_deadline_ack_and_ready_handoff(self) -> None:
        deadline = datetime.now(UTC) - timedelta(seconds=2)
        await self.tracker_repo.get_or_create(self.run_id, deadline_at=deadline)
        parent_key = f"deadline:{self.run_id}:{deadline.isoformat()}"
        async with self.session_factory() as session:
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == parent_key))
            assert parent is not None
            parent_id = parent.id
            payload = parent.payload_json
            assert parent.available_at == deadline

        result = await self.tracker_repo.apply_outbox_event(payload, parent_id, datetime.now(UTC))
        assert result["status"] == "aggregation_queued"
        async with self.session_factory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            successor = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"run-aggregation-handoff:{self.run_id}"))
            assert parent is not None and tracker is not None
            assert parent.consumer_acked_at is not None and successor is not None
            assert tracker.status == RunCompletionTrackerStatus.READY_DEADLINE.value
            assert tracker.cutoff_at == deadline

    async def test_phase7h_analysis_ready_ack_requires_matching_aggregation(self) -> None:
        await self.tracker_repo.get_or_create(self.run_id)
        aggregation_id = uuid4()
        await self.tracker_repo.mark_aggregation_dispatched(self.run_id, aggregation_id)
        bad_aggregation_id = uuid4()
        bad_payload = {
            "schema_version": 1, "event": "analysis_ready",
            "run_id": str(self.run_id), "aggregation_id": str(bad_aggregation_id),
            "result_id": str(uuid4()),
        }
        good_payload = {**bad_payload, "aggregation_id": str(aggregation_id)}
        async with self.session_factory() as session:
            await enqueue(session, message_key=f"analysis-ready:{bad_aggregation_id}",
                          message_type="ANALYSIS_READY", payload=bad_payload)
            await enqueue(session, message_key=f"analysis-ready:{aggregation_id}",
                          message_type="ANALYSIS_READY", payload=good_payload)
            await session.commit()
            bad = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"analysis-ready:{bad_aggregation_id}"))
            good = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"analysis-ready:{aggregation_id}"))
            assert bad is not None and good is not None
            bad_id, good_id = bad.id, good.id

        rejected = await self.tracker_repo.apply_outbox_event(
            bad_payload, bad_id, datetime.now(UTC))
        assert rejected["status"] == "analysis_ready_rejected"
        result = await self.tracker_repo.apply_outbox_event(
            good_payload, good_id, datetime.now(UTC))
        assert result["status"] == "analysis_ready"
        async with self.session_factory() as session:
            bad = await session.get(IntegrationOutbox, bad_id)
            good = await session.get(IntegrationOutbox, good_id)
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(
                OrmRunCompletionTracker.run_id == self.run_id))
            run = await session.get(OrmMonitoringRun, self.run_id)
            assert bad is not None and good is not None and tracker is not None and run is not None
            assert bad.consumer_acked_at is None
            assert good.consumer_acked_at is not None
            assert tracker.status == RunCompletionTrackerStatus.ANALYSIS_READY.value
            assert tracker.aggregation_id == aggregation_id
            assert run.status == MonitoringRunStatus.RUNNING
