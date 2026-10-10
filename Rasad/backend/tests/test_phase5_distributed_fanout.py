"""Tests for Phase 5 Distributed Per-Source Celery Fan-out."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from uuid import UUID, uuid4

from sqlalchemy import select

from app.db.session import SessionFactory, engine
from app.models import BotIdentity, MonitoringJob, Source, User
from app.models.enums import JobStatus, SourceCrawlStatus, SourceStatus, SourceType
from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.monitoring.application.execute_source_task import ExecuteSourceTaskUseCase
from app.modules.monitoring.application.fanout_controller import FanOutSourceTasksUseCase
from app.modules.monitoring.application.types import MonitoringExecutionContext
from app.modules.monitoring.domain.execution import (
    SOURCE_TASK_HEARTBEAT_SECONDS,
    SOURCE_TASK_LEASE_SECONDS,
    ClaimOutcome,
    ClaimStatus,
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


class TestPhase5DistributedFanout(IsolatedAsyncioTestCase):
    """Comprehensive test suite for Phase 5 distributed execution components."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        async with SessionFactory() as session:
            user = await session.scalar(select(User).limit(1))
            self.user_id = user.id if user else uuid4()
            bot = await session.scalar(select(BotIdentity).limit(1))
            self.bot_id = bot.id if bot else uuid4()

            existing_src = await session.scalar(select(Source).limit(1))
            if existing_src is not None:
                self.source = existing_src
            else:
                src = Source(
                    id=uuid4(),
                    title="منبع تست فاز ۵",
                    base_url=f"https://source-{uuid4().hex[:6]}.example.com",
                    type=SourceType.NEWS_SITE,
                    status=SourceStatus.SUCCESS,
                    created_by=self.user_id,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
                session.add(src)
                await session.flush()
                self.source = src

            self.job = MonitoringJob(
                id=uuid4(),
                topic="تست اجرای توزیع‌شده فاز ۵",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=date(2026, 1, 1),
                date_to_gregorian=date(2026, 1, 10),
                requested_by=self.user_id,
                bot_identity_id=self.bot_id,
                status=JobStatus.CRAWLING,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(self.job)
            await session.commit()

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def test_claim_outcome_variants(self):
        """Verify ClaimOutcome typed domain results."""
        token = uuid4()
        exp = datetime.now(UTC) + timedelta(seconds=300)
        claimed = ClaimOutcome.claimed(token, exp, 1)
        self.assertTrue(claimed.is_claimed)
        self.assertEqual(claimed.status, ClaimStatus.CLAIMED)
        self.assertEqual(claimed.claim_token, token)
        self.assertEqual(claimed.lease_expires_at, exp)
        self.assertEqual(claimed.attempt_count, 1)

        term = ClaimOutcome.already_terminal(SourceTaskStatus.COMPLETED)
        self.assertFalse(term.is_claimed)
        self.assertEqual(term.status, ClaimStatus.ALREADY_TERMINAL)
        self.assertEqual(term.current_status, SourceTaskStatus.COMPLETED)

        act = ClaimOutcome.active_lease(exp)
        self.assertFalse(act.is_claimed)
        self.assertEqual(act.status, ClaimStatus.ACTIVE_LEASE)
        self.assertEqual(act.lease_expires_at, exp)

        canc = ClaimOutcome.run_cancelled()
        self.assertFalse(canc.is_claimed)
        self.assertEqual(canc.status, ClaimStatus.RUN_CANCELLED)

        nf = ClaimOutcome.not_found()
        self.assertFalse(nf.is_claimed)
        self.assertEqual(nf.status, ClaimStatus.NOT_FOUND)

    async def test_source_execution_spec_snapshot_roundtrip(self):
        """Verify SourceExecutionSpec serialization and deserialization."""
        spec = SourceExecutionSpec(
            source_id=uuid4(),
            topic="renewable energy",
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 15),
            crawl_config={"concurrency": 2, "max_pages": 10},
            base_url="https://energy.example.org",
            title="Energy Journal",
            spec_version=1,
        )
        data = spec.to_dict()
        self.assertEqual(data["source_id"], str(spec.source_id))
        self.assertEqual(data["topic"], "renewable energy")
        self.assertEqual(data["date_from"], "2026-03-01")
        self.assertEqual(data["date_to"], "2026-03-15")
        self.assertEqual(data["crawl_config"]["max_pages"], 10)

        restored = SourceExecutionSpec.from_dict(data)
        self.assertEqual(restored.source_id, spec.source_id)
        self.assertEqual(restored.topic, spec.topic)
        self.assertEqual(restored.date_from, spec.date_from)
        self.assertEqual(restored.date_to, spec.date_to)
        self.assertEqual(restored.crawl_config, spec.crawl_config)
        self.assertEqual(restored.base_url, spec.base_url)
        self.assertEqual(restored.title, spec.title)
        self.assertEqual(restored.spec_version, 1)

    async def test_claim_explicit_and_lease_renewal(self):
        """Verify claim_task_explicit and renew_lease repository methods."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=self.source.id,
                topic="test",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=self.source.base_url,
                title=self.source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id,
                [{"id": self.source.id, "ordinal": 1, "execution_spec": spec}],
            )
            await session.commit()

            task_id = tasks[0].id

            # 1. Claim explicit
            outcome = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=300)
            await session.commit()
            self.assertTrue(outcome.is_claimed)
            self.assertIsNotNone(outcome.claim_token)
            self.assertEqual(outcome.attempt_count, 1)
            token = outcome.claim_token

            # 2. Second claim while lease active returns ACTIVE_LEASE
            outcome2 = await task_repo.claim_task_explicit(task_id, lease_duration_seconds=300)
            self.assertFalse(outcome2.is_claimed)
            self.assertEqual(outcome2.status, ClaimStatus.ACTIVE_LEASE)

            # 3. Renew lease with valid token
            renewed = await task_repo.renew_lease(task_id, claim_token=token, lease_duration_seconds=300)
            await session.commit()
            self.assertTrue(renewed)

            # 4. Renew lease with wrong token fails
            wrong_token = uuid4()
            renewed_wrong = await task_repo.renew_lease(task_id, claim_token=wrong_token, lease_duration_seconds=300)
            self.assertFalse(renewed_wrong)

    async def test_stale_worker_side_effects_fenced(self):
        """Verify that a worker whose lease expired cannot persist documents or counters."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1, execution_mode="distributed")
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source.id, 1)])
            await session.commit()

            task_id = tasks[0].id

            # Worker A claims with 10s lease in the past
            past_now = datetime.now(UTC) - timedelta(seconds=20)
            claimed, token_a = await task_repo.claim_task(task_id, lease_duration_seconds=10, now=past_now)
            await session.commit()
            self.assertTrue(claimed)

            # Worker B reclaims because lease expired
            current_now = datetime.now(UTC)
            claimed_b, token_b = await task_repo.claim_task(task_id, lease_duration_seconds=300, now=current_now)
            await session.commit()
            self.assertTrue(claimed_b)
            self.assertNotEqual(token_b, token_a)

            # Worker A attempts to complete with stale token_a
            completed_a = await task_repo.complete_task_and_update_run(
                task_id,
                documents_count=10,
                claim_token=token_a,
            )
            await session.commit()
            self.assertFalse(completed_a, "Stale worker completion must be rejected by fence")

            # Verify task remains RUNNING with token_b and 0 terminal sources
            task_after = await task_repo.get_by_id(task_id)
            self.assertEqual(task_after.status, SourceTaskStatus.RUNNING)
            self.assertEqual(task_after.claim_token, token_b)

            run_after = await run_repo.get_by_id(run.id)
            self.assertEqual(run_after.terminal_sources, 0)
            self.assertEqual(run_after.successful_sources, 0)

            # Worker B completes with valid token_b
            completed_b = await task_repo.complete_task_and_update_run(
                task_id,
                documents_count=5,
                claim_token=token_b,
            )
            await session.commit()
            self.assertTrue(completed_b)

            run_final = await run_repo.get_by_id(run.id)
            self.assertEqual(run_final.terminal_sources, 1)
            self.assertEqual(run_final.successful_sources, 1)

    async def test_fanout_controller_dispatches_messages(self):
        """Verify FanOutSourceTasksUseCase creates run, tasks, and dispatches without waiting."""
        dispatched_ids: list[UUID] = []

        class MockDispatcher:
            def dispatch(self, source_task_id: UUID) -> None:
                dispatched_ids.append(source_task_id)

        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            dispatcher = MockDispatcher()

            use_case = FanOutSourceTasksUseCase(run_repo, task_repo, dispatcher)
            context = MonitoringExecutionContext(
                job_id=self.job.id,
                topic="space exploration",
                description="satellites",
                date_from=date(2026, 1, 1),
                date_to=date(2026, 1, 10),
                sources=[{
                    "id": self.source.id,
                    "title": self.source.title,
                    "base_url": self.source.base_url,
                    "crawl_config": {"max_pages": 3},
                }],
            )

            result = await use_case.fan_out(context)
            await session.commit()

            self.assertEqual(result["status"], "dispatched")
            self.assertEqual(result["dispatched_tasks"], 1)
            self.assertEqual(len(dispatched_ids), 1)

            run = await run_repo.get_active_for_job(self.job.id)
            self.assertIsNotNone(run)
            self.assertEqual(run.execution_mode, "distributed")
            self.assertEqual(run.expected_sources, 1)

            task = await task_repo.get_by_id(dispatched_ids[0])
            self.assertIsNotNone(task)
            self.assertIsNotNone(task.execution_spec)
            self.assertEqual(task.execution_spec.topic, "space exploration satellites")
            self.assertEqual(task.status, SourceTaskStatus.PENDING)

    async def test_duplicate_concurrent_claim_only_one_wins(self):
        """Verify that concurrent claims on the same pending task result in exactly one winner."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1, execution_mode="distributed")
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source.id, 1)])
            await session.commit()

            task_id = tasks[0].id

            # Concurrent claims with separate worker sessions
            token1 = uuid4()
            token2 = uuid4()

            async def do_claim(token):
                async with SessionFactory() as worker_session:
                    worker_repo = SqlAlchemySourceTaskRepository(worker_session)
                    out = await worker_repo.claim_task_explicit(task_id, claim_token=token, lease_duration_seconds=300)
                    await worker_session.commit()
                    return out

            res1, res2 = await asyncio.gather(do_claim(token1), do_claim(token2))

            # Exactly one must be claimed
            claimed_count = sum([res1.is_claimed, res2.is_claimed])
            self.assertEqual(claimed_count, 1)

            winner = res1 if res1.is_claimed else res2
            loser = res2 if res1.is_claimed else res1

            self.assertEqual(winner.status, ClaimStatus.CLAIMED)
            self.assertIn(loser.status, {ClaimStatus.ACTIVE_LEASE, ClaimStatus.ALREADY_TERMINAL})

    async def test_execute_source_task_use_case_success_flow(self):
        """Verify ExecuteSourceTaskUseCase claims, runs in-memory crawl, and persists fenced."""
        class MockEngine:
            async def execute_crawl(self, spec):
                return {"pages": 2}, {"documents": []}, {}

        class MockPersister:
            def __init__(self):
                self.persisted = False
            async def persist_success(self, task_id, claim_token, job_id, source_id, crawl_result, prepared_batch, crawl_policy):
                self.persisted = True
                return True
            async def persist_failure(self, task_id, claim_token, job_id, source_id, error):
                return False

        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=self.source.id,
                topic="test topic",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=self.source.base_url,
                title=self.source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id,
                [{"id": self.source.id, "ordinal": 1, "execution_spec": spec}],
            )
            await session.commit()

            task_id = tasks[0].id
            mock_persister = MockPersister()
            use_case = ExecuteSourceTaskUseCase(
                task_repository=task_repo,
                run_repository=run_repo,
                execution_engine=MockEngine(),
                result_persister=mock_persister,
                heartbeat_interval_seconds=1,
            )

            result = await use_case.execute(task_id)
            self.assertEqual(result["status"], "completed")
            self.assertTrue(mock_persister.persisted)

    async def test_execute_source_task_use_case_heartbeat_lost(self):
        """Verify ExecuteSourceTaskUseCase aborts persistence when heartbeat lease renewal fails."""
        class SlowEngine:
            async def execute_crawl(self, spec):
                await asyncio.sleep(0.3)
                return {}, {}, {}

        class MockPersister:
            def __init__(self):
                self.persisted = False
            async def persist_success(self, *args, **kwargs):
                self.persisted = True
                return True
            async def persist_failure(self, *args, **kwargs):
                return False

        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)

            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1, execution_mode="distributed")
            spec = SourceExecutionSpec(
                source_id=self.source.id,
                topic="test topic",
                date_from=None,
                date_to=None,
                crawl_config={},
                base_url=self.source.base_url,
                title=self.source.title,
            )
            tasks = await task_repo.ensure_tasks_for_sources(
                run.id,
                [{"id": self.source.id, "ordinal": 1, "execution_spec": spec}],
            )
            await session.commit()

            task_id = tasks[0].id
            mock_persister = MockPersister()

            # Mock renew_lease to simulate lease theft/failure
            task_repo.renew_lease = lambda *args, **kwargs: asyncio.sleep(0.01, result=False)

            use_case = ExecuteSourceTaskUseCase(
                task_repository=task_repo,
                run_repository=run_repo,
                execution_engine=SlowEngine(),
                result_persister=mock_persister,
                heartbeat_interval_seconds=0.1,
            )

            result = await use_case.execute(task_id)
            self.assertEqual(result["status"], "heartbeat_lost")
            self.assertFalse(mock_persister.persisted, "Persistence must be aborted if heartbeat lost")
