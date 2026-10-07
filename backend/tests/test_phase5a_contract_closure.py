"""Phase 5A: Distributed Fan-out Contract Closure Comprehensive Test Suite."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, date, datetime, timedelta
import inspect
import os
from pathlib import Path
import time
from unittest import IsolatedAsyncioTestCase
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.models.bot_identity import BotIdentity
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_task import SourceTask as OrmSourceTask
from app.models.user import User, UserRole
from app.modules.monitoring.application.execute_source_task import ExecuteSourceTaskUseCase
from app.modules.monitoring.application.fanout_controller import FanOutSourceTasksUseCase
from app.modules.monitoring.application.source_batch import SourceBatchProcessor
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
)
from app.modules.monitoring.application.workflows import MonitoringProgress
from app.modules.monitoring.domain.execution import (
    SOURCE_TASK_HEARTBEAT_SECONDS,
    SOURCE_TASK_LEASE_SECONDS,
    ClaimOutcome,
    ClaimStatus,
    ExecutionMode,
    MonitoringRun,
    MonitoringRunStatus,
    SourceExecutionSpec,
    SourceTask,
    SourceTaskStatus,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
    SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.ports.execution import SourceExecutionEnginePort
from app.shared.domain.errors import ConflictError, ValidationError
from app.workers.celery_app import celery_app
from app.workers.source_tasks import process_source_task


class FakeExecutionEngine(SourceExecutionEnginePort):
    def __init__(self, documents_count: int = 5, sleep_seconds: float = 0.0) -> None:
        self.documents_count = documents_count
        self.sleep_seconds = sleep_seconds
        self.recorded_specs: list[SourceExecutionSpec] = []
        self.call_count = 0

    async def execute_crawl(self, spec: SourceExecutionSpec):
        self.call_count += 1
        self.recorded_specs.append(spec)
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)

        class DummyDiag:
            status = "completed"
            reason_code = "ok"
            summary = "done"
            discovered_total = 10
            selected_total = self.documents_count
            fetched_total = self.documents_count
            successful_pages = self.documents_count
            provider_counts = {}
            selected_provider_counts = {}

            def model_dump(self, mode="json"):
                return {"status": "completed"}

        class DummyCrawlResult:
            pages = [f"page_{i}" for i in range(self.documents_count)]
            discovered_links = ["link1", "link2"]
            diagnostics = DummyDiag()

        class DummyPreparedBatch:
            documents = [{"title": f"Doc {i}", "url": f"http://ex.org/{i}"} for i in range(self.documents_count)]
            rejected = []
            duplicate_count = 0

        class DummyCrawlPolicy:
            max_pages = self.documents_count

        return DummyCrawlResult(), DummyPreparedBatch(), DummyCrawlPolicy()


class TestPhase5AContractClosure(IsolatedAsyncioTestCase):
    """Comprehensive test suite for Phase 5A contract closure."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _create_test_environment(self, session: AsyncSession) -> tuple[MonitoringJob, Source]:
        user = await session.scalar(select(User).limit(1))
        user_id = user.id if user else uuid4()
        bot = await session.scalar(select(BotIdentity).limit(1))
        bot_id = bot.id if bot else uuid4()

        source = Source(
            id=uuid4(),
            title=f"Source 5A {uuid4().hex[:4]}",
            base_url=f"https://source-{uuid4().hex[:6]}.example.org",
            type=SourceType.NEWS_SITE,
            last_status=SourceStatus.SUCCESS,
            created_by=user_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(source)
        await session.flush()

        job = MonitoringJob(
            id=uuid4(),
            topic="climate policy",
            date_from_jalali="1405/01/01",
            date_to_jalali="1405/01/10",
            date_from_gregorian=date(2026, 1, 1),
            date_to_gregorian=date(2026, 1, 10),
            requested_by=user_id,
            bot_identity_id=bot_id,
            status=JobStatus.CRAWLING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        return job, source

    # -----------------------------------------------------------------
    # 1. Mode Immutability Tests (Section 4)
    # -----------------------------------------------------------------
    async def test_execution_mode_immutability_lifecycle(self):
        """Scenario:
        flag=false -> Run A created -> Run A = LEGACY.
        Then flag=true -> continuing Run A remains LEGACY.
        Then Run B created -> Run B = DISTRIBUTED.
        Opposite scenario also verified.
        """
        async with SessionFactory() as session:
            job_a, _ = await self._create_test_environment(session)
            job_b, _ = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)

            # Flag = false -> Run A created as LEGACY
            run_a = await run_repo.ensure_active_run(job_a.id, expected_sources=2, execution_mode="legacy")
            await session.commit()
            assert run_a.execution_mode == "legacy"

            # Flag changes to true -> ensure_active_run on job_a STILL returns LEGACY
            run_a_continued = await run_repo.ensure_active_run(job_a.id, expected_sources=2, execution_mode="distributed")
            assert run_a_continued.id == run_a.id
            assert run_a_continued.execution_mode == "legacy"

            # New job B created with flag = true -> DISTRIBUTED
            run_b = await run_repo.ensure_active_run(job_b.id, expected_sources=2, execution_mode="distributed")
            await session.commit()
            assert run_b.execution_mode == "distributed"

            # Flag changes back to false -> continuing Run B remains DISTRIBUTED
            run_b_continued = await run_repo.ensure_active_run(job_b.id, expected_sources=2, execution_mode="legacy")
            assert run_b_continued.id == run_b.id
            assert run_b_continued.execution_mode == "distributed"

    async def test_execution_mode_mutation_rejected_by_orm_and_db(self):
        """Attempting to update execution_mode of an existing MonitoringRun must fail."""
        async with SessionFactory() as session:
            job, _ = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="legacy")
            await session.commit()

            # 1. ORM level rejection
            orm_run = await session.get(OrmMonitoringRun, run.id)
            orm_run.execution_mode = "distributed"
            with pytest.raises((ConflictError, Exception)):
                await session.commit()
            await session.rollback()

            # 2. Raw SQL / DB trigger rejection
            with pytest.raises(Exception):
                await session.execute(
                    text(f"UPDATE monitoring_runs SET execution_mode = 'distributed' WHERE id = '{run.id}'")
                )
                await session.commit()
            await session.rollback()

            # Verify mode remains legacy
            fresh = await run_repo.get_by_id(run.id)
            assert fresh.execution_mode == "legacy"

    # -----------------------------------------------------------------
    # 2. No Dual Execution Tests (Section 5)
    # -----------------------------------------------------------------
    async def test_no_dual_execution_rejection(self):
        """Single run cannot be executed by both legacy batch processor and distributed fan-out."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            # Create LEGACY run
            run_legacy = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="legacy")
            await session.commit()

            class DummyDispatcher:
                def dispatch(self, task_id): pass

            fanout_uc = FanOutSourceTasksUseCase(run_repo, task_repo, DummyDispatcher())
            context_legacy = MonitoringExecutionContext(
                job_id=job.id,
                topic="test",
                description=None,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                sources=[{"id": source.id, "title": source.title, "base_url": source.base_url}],
                run_id=run_legacy.id,
                execution_mode="legacy",
            )
            with pytest.raises(ConflictError, match="Cannot execute distributed fan-out on a run with execution_mode 'legacy'"):
                await fanout_uc.fan_out(context_legacy)

            context_dist = MonitoringExecutionContext(
                job_id=job.id,
                topic="test",
                description=None,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                sources=[{"id": source.id, "title": source.title, "base_url": source.base_url}],
                run_id=run_legacy.id,
                execution_mode="distributed",
            )
            batch_proc = SourceBatchProcessor(
                cancellation_guard=None,
                progress_recorder=None,
                source_processor=None,
            )
            with pytest.raises(ConflictError, match="Cannot execute legacy batch processor on a run with execution_mode 'distributed'"):
                await batch_proc.process_sources(context_dist)

    # -----------------------------------------------------------------
    # 3. Snapshot Semantics Test (Section 8)
    # -----------------------------------------------------------------
    async def test_snapshot_semantics_after_source_mutation(self):
        """SourceTask must execute with immutable snapshot, unaffected by runtime Source DB mutation."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            initial_url = str(source.base_url)
            initial_config = {"max_pages": 4, "depth": 2}

            spec = SourceExecutionSpec(
                source_id=source.id,
                topic="climate",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                crawl_config=initial_config,
                base_url=initial_url,
                title=source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id, [{"id": source.id, "ordinal": 1, "execution_spec": spec}]
            )
            await session.commit()
            task_id = tasks[0].id

            # Mutate DB source record
            mutated_url = f"https://mutated-host-{uuid4().hex[:6]}.example.org/hijacked"
            source.base_url = mutated_url
            source.crawl_config = {"max_pages": 9999, "depth": 99}
            await session.commit()

            # Execute worker with fake engine
            engine_mock = FakeExecutionEngine(documents_count=2)

            class DummyPersister:
                async def persist_success(self, **kwargs): return True
                async def persist_failure(self, **kwargs): return True

            use_case = ExecuteSourceTaskUseCase(
                task_repository=task_repo,
                run_repository=run_repo,
                execution_engine=engine_mock,
                result_persister=DummyPersister(),
            )
            res = await use_case.execute(task_id)
            assert res["status"] == "completed"

            # Prove worker used snapshot, NOT the mutated DB source
            assert len(engine_mock.recorded_specs) == 1
            used_spec = engine_mock.recorded_specs[0]
            assert used_spec.base_url == initial_url
            assert used_spec.base_url != mutated_url
            assert used_spec.crawl_config == initial_config

            # Verify no secret in spec dictionary
            spec_dict = used_spec.to_dict()
            for k, v in spec_dict.items():
                assert "secret" not in k.lower()
                assert "password" not in str(v).lower()

    # -----------------------------------------------------------------
    # 4. Duplicate Delivery Test (Section 9)
    # -----------------------------------------------------------------
    async def test_duplicate_delivery_concurrency_fencing(self):
        """Two concurrent deliveries for same task: exactly one claims & executes, exactly one updates counters."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=source.id,
                topic="test",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=source.base_url,
                title=source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id, [{"id": source.id, "ordinal": 1, "execution_spec": spec}]
            )
            await session.commit()
            task_id = tasks[0].id

            engine_mock = FakeExecutionEngine(documents_count=3, sleep_seconds=0.1)

            class CountingPersister:
                def __init__(self):
                    self.success_count = 0
                async def persist_success(self, **kwargs):
                    self.success_count += 1
                    return True
                async def persist_failure(self, **kwargs):
                    return True

            persister = CountingPersister()

            async def _worker_exec():
                async with SessionFactory() as w_session:
                    w_task_repo = SqlAlchemySourceTaskRepository(w_session)
                    w_run_repo = SqlAlchemyMonitoringRunRepository(w_session)
                    uc = ExecuteSourceTaskUseCase(
                        task_repository=w_task_repo,
                        run_repository=w_run_repo,
                        execution_engine=engine_mock,
                        result_persister=persister,
                    )
                    return await uc.execute(task_id)

            res1, res2 = await asyncio.gather(_worker_exec(), _worker_exec())
            statuses = {res1["status"], res2["status"]}

            assert "completed" in statuses
            assert "active_lease" in statuses or "already_terminal" in statuses
            assert engine_mock.call_count == 1
            assert persister.success_count == 1

    # -----------------------------------------------------------------
    # 5. Crash / Redelivery Test (Section 10)
    # -----------------------------------------------------------------
    async def test_crash_and_redelivery_reclaims_orphan_task(self):
        """Simulated crash leaves task running with expired lease; redelivery reclaims and terminalizes."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=source.id,
                topic="test",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=source.base_url,
                title=source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id, [{"id": source.id, "ordinal": 1, "execution_spec": spec}]
            )
            await session.commit()
            task_id = tasks[0].id

            # Worker A claims with expired lease in the past (crash simulation)
            past = datetime.now(UTC) - timedelta(seconds=400)
            claimed, token_a = await task_repo.claim_task(task_id, lease_duration_seconds=300, now=past)
            await session.commit()
            assert claimed is True

            # Redelivery B claims task after lease expired
            outcome = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=300)
            await session.commit()
            assert outcome.is_claimed is True
            assert outcome.claim_token != token_a
            assert outcome.attempt_count == 2

            # Terminalize B
            completed = await task_repo.complete_task_and_update_run(
                task_id, documents_count=8, claim_token=outcome.claim_token
            )
            await session.commit()
            assert completed is True

            # Verify task is not orphaned
            t = await task_repo.get_by_id(task_id)
            assert t.status == SourceTaskStatus.COMPLETED
            assert t.attempt_count == 2
            r = await run_repo.get_by_id(run.id)
            assert r.terminal_sources == 1
            assert r.successful_sources == 1

    # -----------------------------------------------------------------
    # 6. Broker Partial-Dispatch Failure & Retry (Section 11)
    # -----------------------------------------------------------------
    async def test_broker_partial_dispatch_failure_and_retry(self):
        """If broker fails on task B, controller raises, retry publishes all without duplicate corruption."""
        async with SessionFactory() as session:
            job, source1 = await self._create_test_environment(session)
            _, source2 = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=2, execution_mode="distributed")
            await session.commit()

            class FlakyDispatcher:
                def __init__(self):
                    self.published: list[UUID] = []
                    self.fail_on: UUID | None = None

                def dispatch(self, source_task_id: UUID) -> None:
                    if self.fail_on and source_task_id == self.fail_on:
                        raise ConnectionError("Redis broker connection dropped!")
                    self.published.append(source_task_id)

            dispatcher = FlakyDispatcher()
            use_case = FanOutSourceTasksUseCase(run_repo, task_repo, dispatcher)
            context = MonitoringExecutionContext(
                job_id=job.id,
                topic="test",
                description=None,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                sources=[
                    {"id": source1.id, "title": source1.title, "base_url": source1.base_url},
                    {"id": source2.id, "title": source2.title, "base_url": source2.base_url},
                ],
                run_id=run.id,
                execution_mode="distributed",
            )

            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(source1.id, 1), (source2.id, 2)])
            await session.commit()

            # Set dispatcher to fail on second task
            dispatcher.fail_on = tasks[1].id
            with pytest.raises(ConnectionError):
                await use_case.fan_out(context)

            assert len(dispatcher.published) == 1
            assert dispatcher.published[0] == tasks[0].id

            # Retry after broker recovery
            dispatcher.fail_on = None
            dispatcher.published.clear()
            result = await use_case.fan_out(context)
            assert result["status"] == "dispatched"
            assert len(dispatcher.published) == 2

    # -----------------------------------------------------------------
    # 7. Controller Non-Waiting (Fire-and-Forget) Proof (Section 12)
    # -----------------------------------------------------------------
    async def test_controller_strictly_fire_and_forget(self):
        """Controller must never wait, join, get(), or poll on AsyncResult-like objects."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            class TrapAsyncResult:
                def __init__(self):
                    self.trapped_calls = []
                def get(self, *args, **kwargs):
                    self.trapped_calls.append("get")
                    raise AssertionError("Controller called get()!")
                def wait(self, *args, **kwargs):
                    self.trapped_calls.append("wait")
                    raise AssertionError("Controller called wait()!")
                def join(self, *args, **kwargs):
                    self.trapped_calls.append("join")
                    raise AssertionError("Controller called join()!")
                def ready(self, *args, **kwargs):
                    self.trapped_calls.append("ready")
                    raise AssertionError("Controller called ready()!")

            trap = TrapAsyncResult()

            class TrapDispatcher:
                def dispatch(self, source_task_id: UUID):
                    return trap

            use_case = FanOutSourceTasksUseCase(run_repo, task_repo, TrapDispatcher())
            context = MonitoringExecutionContext(
                job_id=job.id,
                topic="test",
                description=None,
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                sources=[{"id": source.id, "title": source.title, "base_url": source.base_url}],
                execution_mode="distributed",
            )

            res = await use_case.fan_out(context)
            assert res["status"] == "dispatched"
            assert len(trap.trapped_calls) == 0, "Controller must never call get, wait, join, or ready!"

    # -----------------------------------------------------------------
    # 8. Stale Side-Effect Fencing (Section 15)
    # -----------------------------------------------------------------
    async def test_stale_side_effect_fencing_exact_state(self):
        """Worker A (stale lease) attempting commit must NOT overwrite documents, health, or counters of Worker B."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(source.id, 1)])
            await session.commit()
            task_id = tasks[0].id

            # Worker A claims with 10s lease in the past
            past = datetime.now(UTC) - timedelta(seconds=20)
            claimed_a, token_a = await task_repo.claim_task(task_id, lease_duration_seconds=10, now=past)
            await session.commit()
            assert claimed_a is True

            # Worker B reclaims
            now_b = datetime.now(UTC)
            claimed_b, token_b = await task_repo.claim_task(task_id, lease_duration_seconds=300, now=now_b)
            await session.commit()
            assert claimed_b is True
            assert token_a != token_b

            # Worker B commits 5 documents
            completed_b = await task_repo.complete_task_and_update_run(task_id, documents_count=5, claim_token=token_b)
            await session.commit()
            assert completed_b is True

            # Worker A attempts to commit 10 documents with stale token_a
            completed_a = await task_repo.complete_task_and_update_run(task_id, documents_count=10, claim_token=token_a)
            await session.commit()
            assert completed_a is False

            # Verify state belongs strictly to Worker B
            final_task = await task_repo.get_by_id(task_id)
            assert final_task.documents_count == 5
            assert final_task.status == SourceTaskStatus.COMPLETED

            final_run = await run_repo.get_by_id(run.id)
            assert final_run.terminal_sources == 1
            assert final_run.successful_sources == 1

    # -----------------------------------------------------------------
    # 9. Heartbeat Ownership & Token Loss (Sections 16 & 17)
    # -----------------------------------------------------------------
    async def test_heartbeat_ownership_renewal_and_expiration(self):
        """Worker A renews lease via heartbeat. Reclaim rejected while active; succeeds after expiration."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(source.id, 1)])
            await session.commit()
            task_id = tasks[0].id

            # Worker A claims
            start_time = datetime.now(UTC)
            outcome_a = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=100, now=start_time)
            await session.commit()
            token_a = outcome_a.claim_token

            # Heartbeat renews lease
            t1 = start_time + timedelta(seconds=50)
            renewed = await task_repo.renew_lease(task_id, claim_token=token_a, lease_duration_seconds=100, now=t1)
            await session.commit()
            assert renewed is True

            # Worker B tries to claim at t1 + 30s (lease valid until t1 + 100s) -> rejected
            t2 = t1 + timedelta(seconds=30)
            outcome_b_early = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=100, now=t2)
            assert outcome_b_early.status == ClaimStatus.ACTIVE_LEASE

            # Heartbeat stops, time advances past t1 + 100s
            t3 = t1 + timedelta(seconds=110)
            outcome_b_late = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=100, now=t3)
            await session.commit()
            assert outcome_b_late.status == ClaimStatus.CLAIMED
            assert outcome_b_late.claim_token != token_a

    async def test_heartbeat_token_loss_aborts_persistence(self):
        """If heartbeat renewal fails (token mismatch), worker must abort persistence."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=source.id,
                topic="test",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=source.base_url,
                title=source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id, [{"id": source.id, "ordinal": 1, "execution_spec": spec}]
            )
            await session.commit()
            task_id = tasks[0].id

            class TokenLossRepo:
                async def claim_task_explicit(self, tid, **kwargs):
                    return ClaimOutcome.claimed(uuid4(), datetime.now(UTC) + timedelta(seconds=300), 1)
                async def get_by_id(self, tid):
                    return tasks[0]
                async def renew_lease(self, tid, **kwargs):
                    return False

            class FailingPersister:
                def __init__(self): self.persisted = False
                async def persist_success(self, **kwargs):
                    self.persisted = True
                    return True
                async def persist_failure(self, **kwargs):
                    return True

            persister = FailingPersister()
            engine_mock = FakeExecutionEngine(documents_count=1, sleep_seconds=0.05)
            use_case = ExecuteSourceTaskUseCase(
                task_repository=TokenLossRepo(),
                run_repository=run_repo,
                execution_engine=engine_mock,
                result_persister=persister,
                heartbeat_interval_seconds=0.01,
            )

            res = await use_case.execute(task_id)
            assert res["status"] == "heartbeat_lost"
            assert persister.persisted is False

    # -----------------------------------------------------------------
    # 10. Cancellation Distributed Path (Section 18)
    # -----------------------------------------------------------------
    async def test_cancellation_distributed_semantics(self):
        """Cancelled run before claim produces run_cancelled outcome; no execution."""
        async with SessionFactory() as session:
            job, source = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=1, execution_mode="distributed")
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(source.id, 1)])
            await session.commit()
            task_id = tasks[0].id

            # Cancel run
            await run_repo.finalize_run(run.id, target_status=MonitoringRunStatus.CANCELLED)
            await session.commit()

            # Claim attempt
            outcome = await task_repo.claim_task_explicit(task_id)
            assert outcome.status == ClaimStatus.RUN_CANCELLED
            assert outcome.is_claimed is False

    # -----------------------------------------------------------------
    # 11. Real Celery Concurrency Integration Test (Section 13)
    # -----------------------------------------------------------------
    async def test_real_celery_distributed_concurrency(self):
        """Dispatch 3 real Celery SourceTasks to monitoring.source queue.
        Assert at least 2 tasks execute concurrently with overlapping time intervals.
        """
        async with SessionFactory() as session:
            job, source1 = await self._create_test_environment(session)
            _, source2 = await self._create_test_environment(session)
            _, source3 = await self._create_test_environment(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(job.id, expected_sources=3, execution_mode="distributed")

            spec1 = SourceExecutionSpec(source1.id, "t", date(2026, 1, 1), date(2026, 1, 10), {"sleep": 1.2}, source1.base_url, source1.title)
            spec2 = SourceExecutionSpec(source2.id, "t", date(2026, 1, 1), date(2026, 1, 10), {"sleep": 1.2}, source2.base_url, source2.title)
            spec3 = SourceExecutionSpec(source3.id, "t", date(2026, 1, 1), date(2026, 1, 10), {"sleep": 1.2}, source3.base_url, source3.title)

            tasks = await task_repo.ensure_tasks_for_sources(
                run.id,
                [
                    {"id": source1.id, "ordinal": 1, "execution_spec": spec1},
                    {"id": source2.id, "ordinal": 2, "execution_spec": spec2},
                    {"id": source3.id, "ordinal": 3, "execution_spec": spec3},
                ],
            )
            await session.commit()

            task_ids = [t.id for t in tasks]

            # Dispatch all 3 tasks to Celery queue monitoring.source
            for tid in task_ids:
                celery_app.send_task(
                    "monitoring.process_source_task",
                    args=[str(tid)],
                    queue="monitoring.source",
                )

            # Wait for all 3 tasks to reach terminal state in DB
            deadline = time.time() + 45
            completed_tasks = []
            while time.time() < deadline:
                async with SessionFactory() as check_session:
                    c_repo = SqlAlchemySourceTaskRepository(check_session)
                    all_done = True
                    current_completed = []
                    for tid in task_ids:
                        task_rec = await c_repo.get_by_id(tid)
                        if task_rec.status not in (SourceTaskStatus.COMPLETED, SourceTaskStatus.FAILED):
                            all_done = False
                            break
                        current_completed.append(task_rec)
                    if all_done:
                        completed_tasks = current_completed
                        break
                await asyncio.sleep(0.5)

            assert len(completed_tasks) == 3, f"Not all 3 tasks finished in time! Done: {len(completed_tasks)}"

            intervals = [(t.started_at, t.finished_at) for t in completed_tasks if t.started_at and t.finished_at]
            has_overlap = False
            if len(intervals) >= 2:
                for i in range(len(intervals)):
                    for j in range(i + 1, len(intervals)):
                        s1, e1 = intervals[i]
                        s2, e2 = intervals[j]
                        if max(s1, s2) <= min(e1, e2):
                            has_overlap = True
                            break
                    if has_overlap:
                        break

            assert has_overlap, f"Expected real Celery worker overlap between tasks, but intervals were: {intervals}"


# =====================================================================
# Synchronous Static & Config Tests (Sections 6, 7, 14, 19, 20, 21, 24, 25)
# =====================================================================
def test_redis_visibility_timeout_and_delivery_invariants():
    """Verify single-source-of-truth constants and Celery delivery guarantees."""
    assert settings.celery_source_visibility_timeout_seconds > SOURCE_TASK_LEASE_SECONDS
    assert settings.celery_source_visibility_timeout_seconds == 1800
    assert SOURCE_TASK_LEASE_SECONDS == 300
    assert SOURCE_TASK_HEARTBEAT_SECONDS == 90

    # Task settings
    assert process_source_task.acks_late is True
    assert process_source_task.reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_routes["monitoring.process_source_task"]["queue"] == "monitoring.source"


def test_monitoring_application_has_zero_celery_or_redis_imports():
    """Monitoring Application layer must have 0 imports from Celery or Redis."""
    app_dir = Path("/opt/rasad10/backend/app/modules/monitoring/application")
    violations = []
    for py_file in app_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if "celery" in alias.name or "redis" in alias.name:
                        violations.append(f"{py_file.name} imports {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "celery" in mod or "redis" in mod:
                    violations.append(f"{py_file.name} imports from {mod}")
    assert not violations, f"Architecture violations found: {violations}"


def test_celery_source_task_entrypoint_is_transport_only():
    """The Celery task function must only unwrap the ID and invoke the composition root."""
    source_lines = inspect.getsource(process_source_task)
    assert "SessionFactory" not in source_lines
    assert "select(" not in source_lines
    assert "execute_crawl" not in source_lines
    assert "run_source_task_worker" in source_lines


def test_distributed_source_task_does_not_analyze_or_fan_in():
    """Verify ExecuteSourceTaskUseCase and run_source_task_worker do NOT perform analysis or fan-in."""
    uc_source = inspect.getsource(ExecuteSourceTaskUseCase)
    assert "AnalysisJobCoordinator" not in uc_source
    assert "analyze_job" not in uc_source
    assert "report" not in uc_source
    assert "CompletionTracker" not in uc_source
    assert "chord" not in uc_source
    assert "group" not in uc_source
