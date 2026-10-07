"""Tests for MonitoringRun and SourceTask database repositories under concurrency and crash recovery."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from sqlalchemy import select
from pathlib import Path
from tempfile import TemporaryDirectory
from app.models.integration_outbox import IntegrationOutbox
from app.modules.monitoring.infrastructure.crawl_checkpoint import FencedCrawlCheckpoint, CrawlCheckpointFenceLost
from app.modules.monitoring.domain.execution import SourceExecutionSpec
from app.modules.crawling.infrastructure.worker_admission import WorkerAdmission
from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.models import BotIdentity, MonitoringJob, Source, User
from app.models.enums import JobStatus, SourceStatus, SourceType
from app.modules.monitoring.domain.execution import (
    MonitoringRun,
    MonitoringRunStatus,
    SourceTaskStatus,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
    SqlAlchemySourceTaskRepository,
)
from app.shared.domain.errors import ValidationError


class ExecutionRepositoryTests(IsolatedAsyncioTestCase):
    async def test_worker_admission_is_shared_between_independent_instances(self):
        url = 'https://' + uuid4().hex + '.example.invalid'
        gates = [WorkerAdmission(settings.redis_url, url, concurrency=1) for _ in range(2)]
        active, maximum = 0, 0
        async def request(gate):
            nonlocal active, maximum
            async with gate.slot():
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(.015)
                active -= 1
        await asyncio.gather(*(request(gates[i % 2]) for i in range(4)))
        self.assertEqual(maximum, 1)

    async def test_durable_continuation_has_no_terminal_counter_and_fences_old_owner(self):
        async with SessionFactory() as session:
            run = await SqlAlchemyMonitoringRunRepository(session).ensure_active_run(self.job.id, expected_sources=1)
            repository = SqlAlchemySourceTaskRepository(session)
            tasks = await repository.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claim = await repository.claim_task_explicit(task_id)
        spec = SourceExecutionSpec(self.source1.id, 'synthetic', self.job.date_from_gregorian,
            self.job.date_to_gregorian, {}, 'https://example.com', 'synthetic')
        with TemporaryDirectory() as directory:
            old = FencedCrawlCheckpoint(SessionFactory, task_id, claim.claim_token, spec, root=Path(directory))
            await old.save('plan', {'selected': []})
            async with SessionFactory() as session:
                repository = SqlAlchemySourceTaskRepository(session)
                deferred_at = datetime.now(UTC)
                self.assertTrue(await repository.defer_task(task_id, claim_token=claim.claim_token, delay_seconds=45))
                self.assertFalse(await repository.defer_task(task_id, claim_token=claim.claim_token))
                updated = await SqlAlchemyMonitoringRunRepository(session).get_by_id(run.id)
                self.assertEqual(updated.terminal_sources, 0)
                intents = (await session.scalars(select(IntegrationOutbox).where(
                    IntegrationOutbox.message_key == f'source-continuation:{task_id}:1'))).all()
                self.assertEqual(len(intents), 1)
                self.assertEqual(intents[0].message_type, 'SOURCE_EXECUTION')
                self.assertGreaterEqual(intents[0].available_at, deferred_at + timedelta(seconds=45))
                new_claim = await repository.claim_task_explicit(task_id)
            with self.assertRaises(CrawlCheckpointFenceLost):
                await old.save('plan', {'selected': ['stale']})
            recovered = FencedCrawlCheckpoint(SessionFactory, task_id, new_claim.claim_token, spec, root=Path(directory))
            self.assertEqual(await recovered.load(), {'plan': {'selected': []}})
            async with SessionFactory() as session:
                await SqlAlchemyMonitoringRunRepository(session).transition_status(run.id, MonitoringRunStatus.CANCELLED)
                await session.commit()
            with self.assertRaises(CrawlCheckpointFenceLost):
                await recovered.save('plan', {'selected': ['cancelled']})
            async with SessionFactory() as session:
                self.assertFalse(await SqlAlchemySourceTaskRepository(session).defer_task(task_id, claim_token=new_claim.claim_token))

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        async with SessionFactory() as session:
            user = await session.scalar(select(User).limit(1))
            self.user_id = user.id if user else uuid4()
            bot = await session.scalar(select(BotIdentity).limit(1))
            self.bot_id = bot.id if bot else uuid4()
            sources = (await session.scalars(select(Source).limit(2))).all()
            if len(sources) >= 2:
                self.source1 = sources[0]
                self.source2 = sources[1]
            else:
                s1 = Source(id=uuid4(), title="منبع تست ۱", base_url="https://source1.example.com", source_type=SourceType.NEWS_SITE, status=SourceStatus.SUCCESS)
                s2 = Source(id=uuid4(), title="منبع تست ۲", base_url="https://source2.example.com", source_type=SourceType.NEWS_SITE, status=SourceStatus.SUCCESS)
                session.add_all([s1, s2])
                await session.flush()
                self.source1 = s1
                self.source2 = s2

            self.job = MonitoringJob(
                id=uuid4(),
                topic="تست مخزن اجرا فاز ۴A",
                date_from_jalali="1405/01/01",
                date_to_jalali="1405/01/10",
                date_from_gregorian=datetime(2026, 3, 21).date(),
                date_to_gregorian=datetime(2026, 3, 31).date(),
                status=JobStatus.PENDING,
                requested_by=self.user_id,
                bot_identity_id=self.bot_id,
            )
            session.add(self.job)
            await session.commit()

    async def asyncTearDown(self) -> None:
        try:
            async with SessionFactory() as session:
                job = await session.get(MonitoringJob, self.job.id)
                if job:
                    await session.delete(job)
                    await session.commit()
        finally:
            await engine.dispose()

    # 1. concurrent_active_run_initialization
    async def test_concurrent_active_run_initialization(self) -> None:
        """Two concurrent coroutines call ensure_active_run: exactly one active run exists."""
        async def init_run():
            async with SessionFactory() as session:
                repo = SqlAlchemyMonitoringRunRepository(session)
                run = await repo.ensure_active_run(self.job.id, expected_sources=2)
                await session.commit()
                return run.id

        run_id1, run_id2 = await asyncio.gather(init_run(), init_run())
        self.assertEqual(run_id1, run_id2)

        async with SessionFactory() as session:
            repo = SqlAlchemyMonitoringRunRepository(session)
            active = await repo.get_active_for_job(self.job.id)
            self.assertIsNotNone(active)
            self.assertEqual(active.id, run_id1)

    # 2. duplicate_success_completion_is_idempotent
    async def test_duplicate_success_completion_is_idempotent(self) -> None:
        """Calling complete_task_and_update_run twice increments counters only once."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=2)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claimed, token = await task_repo.claim_task(task_id)
            await session.commit()
            self.assertTrue(claimed)

        # Call complete first time
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            first_res = await task_repo.complete_task_and_update_run(task_id, documents_count=12, claim_token=token)
            await session.commit()
            self.assertTrue(first_res)

        # Call complete second time (retry)
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            second_res = await task_repo.complete_task_and_update_run(task_id, documents_count=12, claim_token=token)
            await session.commit()
            self.assertFalse(second_res)

        # Counters must reflect exactly 1 terminal and 1 successful
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            updated_run = await run_repo.get_by_id(run.id)
            self.assertEqual(updated_run.terminal_sources, 1)
            self.assertEqual(updated_run.successful_sources, 1)
            self.assertEqual(updated_run.failed_sources, 0)

    # 3. duplicate_failure_completion_is_idempotent
    async def test_duplicate_failure_completion_is_idempotent(self) -> None:
        """Calling fail_task_and_update_run twice increments failed_sources only once."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=2)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claimed, token = await task_repo.claim_task(task_id)
            await session.commit()

        # First fail
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res1 = await task_repo.fail_task_and_update_run(task_id, error_code="TIMEOUT", error_message="Timed out", claim_token=token)
            await session.commit()
            self.assertTrue(res1)

        # Second fail
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res2 = await task_repo.fail_task_and_update_run(task_id, error_code="TIMEOUT", error_message="Timed out", claim_token=token)
            await session.commit()
            self.assertFalse(res2)

        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            updated_run = await run_repo.get_by_id(run.id)
            self.assertEqual(updated_run.terminal_sources, 1)
            self.assertEqual(updated_run.failed_sources, 1)
            self.assertEqual(updated_run.successful_sources, 0)

    # 4. competing_success_failure_only_one_wins
    async def test_competing_success_failure_only_one_wins(self) -> None:
        """Two concurrent coroutines trying to complete vs fail the same task: exactly one wins."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=2)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claimed, token = await task_repo.claim_task(task_id)
            await session.commit()

        async def do_complete():
            async with SessionFactory() as session:
                repo = SqlAlchemySourceTaskRepository(session)
                res = await repo.complete_task_and_update_run(task_id, documents_count=5, claim_token=token)
                await session.commit()
                return res

        async def do_fail():
            async with SessionFactory() as session:
                repo = SqlAlchemySourceTaskRepository(session)
                res = await repo.fail_task_and_update_run(task_id, error_code="ERR", error_message="Err", claim_token=token)
                await session.commit()
                return res

        res_complete, res_fail = await asyncio.gather(do_complete(), do_fail())
        self.assertTrue((res_complete and not res_fail) or (res_fail and not res_complete))

        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            updated_run = await run_repo.get_by_id(run.id)
            self.assertEqual(updated_run.terminal_sources, 1)
            self.assertEqual(updated_run.successful_sources + updated_run.failed_sources, 1)

    # 5. expired_running_task_can_be_reclaimed
    async def test_expired_running_task_can_be_reclaimed(self) -> None:
        """A running task whose lease is expired can be reclaimed by another worker."""
        past = datetime.now(UTC) - timedelta(seconds=500)
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            # Claim with past time and short lease (expired)
            claimed, token1 = await task_repo.claim_task(task_id, lease_duration_seconds=100, now=past)
            await session.commit()
            self.assertTrue(claimed)

        # Reclaim now
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            reclaimed, token2 = await task_repo.claim_task(task_id, lease_duration_seconds=300)
            await session.commit()
            self.assertTrue(reclaimed)
            self.assertNotEqual(token1, token2)

            task = await task_repo.get_by_id(task_id)
            self.assertEqual(task.status, SourceTaskStatus.RUNNING)
            self.assertEqual(task.attempt_count, 2)
            self.assertEqual(task.claim_token, token2)

    # 6. non_expired_running_task_cannot_be_reclaimed
    async def test_non_expired_running_task_cannot_be_reclaimed(self) -> None:
        """A running task with active lease cannot be reclaimed."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claimed, token = await task_repo.claim_task(task_id, lease_duration_seconds=300)
            await session.commit()
            self.assertTrue(claimed)

            # Attempt immediate reclaim
            reclaimed, token2 = await task_repo.claim_task(task_id, lease_duration_seconds=300)
            await session.commit()
            self.assertFalse(reclaimed)
            self.assertIsNone(token2)

    # 7. stale_claimant_cannot_complete_after_reclaim
    async def test_stale_claimant_cannot_complete_after_reclaim(self) -> None:
        """Worker A claims, lease expires, Worker B reclaims. Worker A cannot complete."""
        past = datetime.now(UTC) - timedelta(seconds=500)
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            # Worker A claims in past
            _, token_a = await task_repo.claim_task(task_id, lease_duration_seconds=100, now=past)
            await session.commit()

        # Worker B reclaims
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            _, token_b = await task_repo.claim_task(task_id, lease_duration_seconds=300)
            await session.commit()

        # Worker A attempts late completion
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res_a = await task_repo.complete_task_and_update_run(task_id, documents_count=10, claim_token=token_a)
            await session.commit()
            self.assertFalse(res_a)

        # Worker B completes
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res_b = await task_repo.complete_task_and_update_run(task_id, documents_count=15, claim_token=token_b)
            await session.commit()
            self.assertTrue(res_b)

            run_repo = SqlAlchemyMonitoringRunRepository(session)
            updated_run = await run_repo.get_by_id(run.id)
            self.assertEqual(updated_run.terminal_sources, 1)
            self.assertEqual(updated_run.successful_sources, 1)

            task = await task_repo.get_by_id(task_id)
            self.assertEqual(task.status, SourceTaskStatus.COMPLETED)
            self.assertEqual(task.documents_count, 15)

    # 8. cancelled_run_tasks_are_not_reclaimed
    async def test_cancelled_run_tasks_are_not_reclaimed(self) -> None:
        """An expired running task whose parent run is CANCELLED cannot be reclaimed."""
        past = datetime.now(UTC) - timedelta(seconds=500)
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            await task_repo.claim_task(task_id, lease_duration_seconds=100, now=past)
            # Cancel parent run
            await run_repo.transition_status(run.id, MonitoringRunStatus.CANCELLED, datetime.now(UTC))
            await session.commit()

        # Attempt reclaim
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            reclaimed, _ = await task_repo.claim_task(task_id, lease_duration_seconds=300)
            await session.commit()
            self.assertFalse(reclaimed)

    # 9. run_cannot_complete_before_all_tasks_terminal
    async def test_run_cannot_complete_before_all_tasks_terminal(self) -> None:
        """finalize_run raises ValidationError if terminal_sources != expected_sources."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=5)
            await session.commit()

            with self.assertRaises(ValidationError):
                await run_repo.finalize_run(run.id, target_status=MonitoringRunStatus.COMPLETED)

    # 10. run_finalization_is_idempotent
    async def test_run_finalization_is_idempotent(self) -> None:
        """Calling finalize_run multiple times on completed run preserves finished_at."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=2)
            await run_repo.update_counters(run.id, terminal_delta=2, success_delta=2)
            t_fin = datetime(2026, 9, 23, 8, 0, 0, tzinfo=UTC)
            finalized1 = await run_repo.finalize_run(run.id, MonitoringRunStatus.COMPLETED, now=t_fin)
            await session.commit()
            self.assertEqual(finalized1.status, MonitoringRunStatus.COMPLETED)
            self.assertEqual(finalized1.finished_at, t_fin)

            # Second call
            t_second = datetime(2026, 9, 23, 9, 0, 0, tzinfo=UTC)
            finalized2 = await run_repo.finalize_run(run.id, MonitoringRunStatus.COMPLETED, now=t_second)
            await session.commit()
            self.assertEqual(finalized2.finished_at, t_fin)

    # 11. failure_injection_rolls_back_task_and_counters
    async def test_failure_injection_rolls_back_task_and_counters(self) -> None:
        """If transaction aborts, neither the task state nor the run counters are committed."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            _, token = await task_repo.claim_task(task_id)
            await session.commit()

        # Injected failure: complete task and update run, but raise Exception before commit
        try:
            async with SessionFactory() as session:
                task_repo = SqlAlchemySourceTaskRepository(session)
                await task_repo.complete_task_and_update_run(task_id, documents_count=10, claim_token=token)
                raise RuntimeError("Artificial Failure Injected!")
        except RuntimeError:
            pass

        # Verify rollback: task is still RUNNING, terminal_sources is still 0
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task = await task_repo.get_by_id(task_id)
            current_run = await run_repo.get_by_id(run.id)
            self.assertEqual(task.status, SourceTaskStatus.RUNNING)
            self.assertEqual(current_run.terminal_sources, 0)
            self.assertEqual(current_run.successful_sources, 0)

    # 12. claim_task_default_lease_duration_matches_constant
    async def test_claim_task_default_lease_duration_matches_constant(self) -> None:
        """claim_task without explicit lease_duration_seconds uses SOURCE_TASK_LEASE_SECONDS."""
        from app.modules.monitoring.domain.execution import SOURCE_TASK_LEASE_SECONDS
        self.assertEqual(SOURCE_TASK_LEASE_SECONDS, 300)
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            claimed, token = await task_repo.claim_task(task_id)
            await session.commit()
            self.assertTrue(claimed)

            task = await task_repo.get_by_id(task_id)
            self.assertIsNotNone(task)
            self.assertEqual(task.status, SourceTaskStatus.RUNNING)
            self.assertEqual(task.claim_token, token)
            self.assertTrue(task.lease_expires_at > task.claimed_at)
            delta = (task.lease_expires_at - task.claimed_at).total_seconds()
            self.assertAlmostEqual(delta, 300.0, places=1)

    # 13. db_check_constraint_rejects_running_task_with_null_token
    async def test_db_check_constraint_rejects_running_task_with_null_token(self) -> None:
        """Database CHECK constraint ck_source_tasks_running_lease_invariants rejects status=running with null claim_token."""
        from sqlalchemy.exc import IntegrityError
        from app.models.source_task import SourceTask as OrmSourceTask
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=1)
            await session.commit()

            # Attempt direct insert with running status but null claim_token
            now = datetime.now(UTC)
            invalid_task = OrmSourceTask(
                id=uuid4(),
                run_id=run.id,
                source_id=self.source1.id,
                ordinal=99,
                status=SourceTaskStatus.RUNNING,
                claim_token=None,
                claimed_at=now,
                lease_expires_at=now + timedelta(seconds=300),
            )
            session.add(invalid_task)
            with self.assertRaises(IntegrityError) as ctx:
                await session.flush()
            self.assertIn("ck_source_tasks_running_lease_invariants", str(ctx.exception).lower())
            await session.rollback()

    # 14. invalid_or_unclaimed_token_cannot_complete_or_fail_task
    async def test_invalid_or_unclaimed_token_cannot_complete_or_fail_task(self) -> None:
        """Calling complete or fail with invalid or un-claimed token returns False without modifying counters."""
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            run = await run_repo.ensure_active_run(self.job.id, expected_sources=2)
            tasks = await task_repo.ensure_tasks_for_sources(run.id, [(self.source1.id, 1)])
            task_id = tasks[0].id
            await session.commit()

        # Attempt to complete an un-claimed (PENDING) task
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res = await task_repo.complete_task_and_update_run(task_id, documents_count=5, claim_token=uuid4())
            await session.commit()
            self.assertFalse(res)

        # Attempt to fail an un-claimed (PENDING) task
        async with SessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            res = await task_repo.fail_task_and_update_run(task_id, error_code="ERR", error_message="msg", claim_token=uuid4())
            await session.commit()
            self.assertFalse(res)

        # Counters remain 0
        async with SessionFactory() as session:
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            task_repo = SqlAlchemySourceTaskRepository(session)
            r = await run_repo.get_by_id(run.id)
            t = await task_repo.get_by_id(task_id)
            self.assertEqual(r.terminal_sources, 0)
            self.assertEqual(r.successful_sources, 0)
            self.assertEqual(t.status, SourceTaskStatus.PENDING)
