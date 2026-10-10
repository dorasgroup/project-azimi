"""Desired-safety regressions. Real dedicated PostgreSQL, never production env."""

import os
import asyncio
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch
from datetime import UTC, date, datetime, timedelta
from unittest import IsolatedAsyncioTestCase, skipUnless
from uuid import uuid4

from sqlalchemy import select, text, func, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from scripts.phase8a3_isolation import assert_isolated
from app.models import (
    BotIdentity, MonitoringJob, MonitoringRun, Source, SourceTask, SourceType, User,
    OrmSourceAnalysis, OrmRunAggregation,
    OrmRunCompletionTracker, OrmSourceAnalysisResult, OrmRunAggregationInput,
    OrmAggregatedAnalysisResult, IntegrationOutbox, OrmRunCompletionEvent,
)
from app.modules.monitoring.domain.execution import MonitoringRunStatus, SourceExecutionSpec
from app.modules.monitoring.infrastructure.execution_repository import SqlAlchemyMonitoringRunRepository
from app.modules.analysis.infrastructure.source_analysis_repository import SqlAlchemySourceAnalysisRepository
from app.modules.analysis.infrastructure.run_aggregation_repository import SqlAlchemyRunAggregationRepository
from app.modules.analysis.domain.run_aggregation import AggregatedAnalysisResultData
from app.modules.analysis.domain.source_analysis import SourceAnalysisResultData
from app.modules.analysis.infrastructure.source_analysis_result_repository import SqlAlchemySourceAnalysisResultRepository
from app.bootstrap.run_cancellation import provide_run_cancellation, SqlAlchemyRunCancellationTransaction
from app.modules.monitoring.application.cancel_distributed_run import CancelDistributedMonitoringRun
from app.modules.monitoring.infrastructure.execution_repository import SqlAlchemySourceTaskRepository


@skipUnless(os.getenv("PHASE8A3_DB_URL"), "explicit isolated PostgreSQL required")
class CancellationPostgresTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db = os.environ["PHASE8A3_DB_URL"]
        assert_isolated(db, os.environ["PHASE8A3_REDIS_URL"])
        self.engine = create_async_engine(db, poolclass=NullPool)
        self.sf = async_sessionmaker(self.engine, expire_on_commit=False)
        # Read-only live identity check precedes first fixture write.
        async with self.engine.connect() as c:
            self.assertEqual(await c.scalar(text("select current_database()")), urlsplit(db).path[1:])
        self.run_id, self.task_id, self.analysis_id, self.agg_id = [uuid4() for _ in range(4)]
        self.now = datetime.now(UTC)
        async with self.sf() as s, s.begin():
            u = User(full_name="Isolated lifecycle", username=uuid4().hex,
                     email=f"{uuid4().hex}@example.invalid", password_hash="synthetic")
            s.add(u)
            await s.flush()
            bot = BotIdentity(title="Synthetic", role_definition="synthetic", created_by=u.id)
            src = Source(title="Synthetic", base_url=f"https://{uuid4().hex}.example.invalid",
                         type=SourceType.NEWS_SITE, created_by=u.id)
            s.add_all([bot, src])
            await s.flush()
            self.source_id = src.id
            job = MonitoringJob(topic="synthetic", requested_by=u.id, bot_identity_id=bot.id,
                                date_from_jalali="1405/07/01", date_to_jalali="1405/07/01",
                                date_from_gregorian=date.today(), date_to_gregorian=date.today())
            s.add(job)
            await s.flush()
            self.job_id = job.id
            s.add(MonitoringRun(id=self.run_id, monitoring_job_id=job.id, status="running",
                                execution_mode="distributed", expected_sources=1))
            await s.flush()
            s.add(SourceTask(id=self.task_id, run_id=self.run_id, source_id=src.id, ordinal=1))
            await s.flush()
            s.add(OrmSourceAnalysis(id=self.analysis_id, source_task_id=self.task_id,
                                   run_id=self.run_id, source_id=src.id, status="running",
                                   attempt_count=1, claim_token=uuid4(),
                                   lease_expires_at=self.now-timedelta(seconds=10)))
            s.add(OrmRunAggregation(id=self.agg_id, run_id=self.run_id, status="running",
                                   readiness_reason="all_terminal", manifest_digest="a"*64,
                                   run_analysis_spec_digest="b"*64, attempt_count=1,
                                   claim_token=uuid4(), lease_expires_at=self.now-timedelta(seconds=10)))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def parent_only_cancel(self):
        async with self.sf() as s, s.begin():
            await SqlAlchemyMonitoringRunRepository(s).transition_status(
                self.run_id, MonitoringRunStatus.CANCELLED)

    def result(self):
        return AggregatedAnalysisResultData(
            id=uuid4(), aggregation_id=self.agg_id, run_id=self.run_id,
            schema_version="1.0", manifest_digest="a"*64, readiness_reason="all_terminal",
            expected_sources=1, successful_sources=0, failed_sources=0, cancelled_sources=0,
            analysis_successful=0, analysis_failed=0, analysis_cancelled=0,
            summary="synthetic", findings=[], coverage_metadata={}, provenance_references=[],
            raw_result=None, created_at=self.now)

    async def test_A_parent_cancel_blocks_expired_analysis_claim(self):
        await self.parent_only_cancel()
        async with self.sf() as s:
            outcome = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
            self.assertNotEqual(outcome.status.value, "claimed")

    async def test_B_parent_cancel_blocks_expired_aggregation_claim(self):
        await self.parent_only_cancel()
        outcome = await SqlAlchemyRunAggregationRepository(self.sf).claim_explicit(
            self.agg_id, uuid4(), 60, self.now)
        self.assertNotEqual(outcome.status.value, "claimed")

    async def test_C_parent_cancel_rejects_old_analysis_completion(self):
        async with self.sf() as s:
            claim = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        self.assertEqual(claim.status.value, "claimed")
        await self.parent_only_cancel()
        async with self.sf() as s:
            self.assertFalse(await SqlAlchemySourceAnalysisRepository(s).complete_analysis(
                self.analysis_id, claim.claim_token))

    async def test_D_parent_cancel_rejects_old_aggregate_result(self):
        repo = SqlAlchemyRunAggregationRepository(self.sf)
        token = uuid4()
        claim = await repo.claim_explicit(self.agg_id, token, 60, self.now)
        self.assertEqual(claim.status.value, "claimed")
        await self.parent_only_cancel()
        self.assertFalse(await repo.save_result_and_complete(self.agg_id, token, self.result(), self.now))

    async def cancel(self, **kwargs):
        return await provide_run_cancellation(self.sf).execute(self.run_id, **kwargs)

    async def tracker(self, status="open"):
        async with self.sf() as s, s.begin():
            s.add(OrmRunCompletionTracker(run_id=self.run_id, status=status,
                                         deadline_at=self.now, ready_at=self.now,
                                         analysis_terminal_count=1, analysis_successful_count=1))

    async def state(self):
        async with self.sf() as s:
            run = await s.get(MonitoringRun, self.run_id)
            task = await s.get(SourceTask, self.task_id)
            analysis = await s.get(OrmSourceAnalysis, self.analysis_id)
            agg = await s.get(OrmRunAggregation, self.agg_id)
            tracker = await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == self.run_id))
            return {
                "run": run.status.value, "counters": (run.terminal_sources, run.successful_sources, run.failed_sources, run.cancelled_sources),
                "task": task.status.value, "analysis": analysis.status.value, "aggregation": agg.status,
                "tracker": tracker.status if tracker else None,
                "attempts": (task.attempt_count, analysis.attempt_count, agg.attempt_count),
                "tokens": (task.claim_token, analysis.claim_token, agg.claim_token),
                "leases": (task.lease_expires_at, analysis.lease_expires_at, agg.lease_expires_at),
            }

    async def outbox_count(self):
        async with self.sf() as s:
            return await s.scalar(select(func.count()).select_from(IntegrationOutbox))

    async def test_coordinated_closure_counter_idempotency_and_no_business_intents(self):
        await self.tracker()
        before = await self.state()
        intents = await self.outbox_count()
        first = await self.cancel(expected_status=MonitoringRunStatus.RUNNING)
        self.assertEqual((first.status, first.sources_cancelled, first.analyses_cancelled,
                          first.aggregations_cancelled, first.trackers_cancelled), ("cancelled", 1, 1, 1, 1))
        after = await self.state()
        self.assertEqual([after[k] for k in ("run", "task", "analysis", "aggregation", "tracker")], ["cancelled"]*5)
        self.assertEqual(after["counters"], (1, 0, 0, 1))
        self.assertEqual(after["attempts"], before["attempts"])
        self.assertEqual(after["tokens"], (None,)*3)
        self.assertEqual(after["leases"], (None,)*3)
        self.assertEqual((await self.cancel()).status, "already_terminal")
        self.assertEqual(await self.state(), after)
        self.assertEqual(await self.outbox_count(), intents)
        async with self.sf() as s:
            self.assertEqual((await s.get(MonitoringJob, self.job_id)).status.value, "pending")
            tracker = await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == self.run_id))
            self.assertEqual((tracker.analysis_terminal_count, tracker.analysis_successful_count, tracker.ready_at), (1, 1, self.now))

    async def test_missing_tracker_remains_missing_and_error_spec_history_unchanged(self):
        async with self.sf() as s, s.begin():
            await s.execute(update(OrmSourceAnalysis).where(OrmSourceAnalysis.id == self.analysis_id)
                .values(error_code="HISTORICAL_RETRY", error_message="synthetic", input_digest="c"*64, analysis_spec={"topic":"synthetic"}))
        await self.cancel()
        self.assertIsNone((await self.state())["tracker"])
        async with self.sf() as s:
            a = await s.get(OrmSourceAnalysis, self.analysis_id)
            self.assertEqual((a.error_code, a.error_message, a.input_digest, a.analysis_spec),
                             ("HISTORICAL_RETRY", "synthetic", "c"*64, {"topic":"synthetic"}))

    async def test_all_old_tokens_and_duplicate_claims_fenced(self):
        async with self.sf() as s:
            source_claim = await SqlAlchemySourceTaskRepository(s).claim_task_explicit(self.task_id)
        async with self.sf() as s:
            analysis_claim = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        agg_repo = SqlAlchemyRunAggregationRepository(self.sf)
        agg_token = uuid4()
        await agg_repo.claim_explicit(self.agg_id, agg_token, 60, self.now)
        await self.cancel()
        intents = await self.outbox_count()
        before = await self.state()
        async with self.sf() as s:
            repo = SqlAlchemySourceAnalysisRepository(s)
            token = analysis_claim.claim_token
            self.assertFalse(await repo.renew_lease(self.analysis_id, token))
            self.assertFalse(await repo.complete_analysis(self.analysis_id, token))
            self.assertFalse(await repo.fail_analysis(self.analysis_id, token, "synthetic", "synthetic"))
            self.assertFalse(await repo.release_claim(self.analysis_id, token))
            self.assertFalse(await SqlAlchemySourceAnalysisResultRepository(s).save_result_fenced(
                self.analysis_id, token, SourceAnalysisResultData("c"*64,"synthetic",[],{})))
            self.assertNotEqual((await repo.claim_explicit(self.analysis_id)).status.value, "claimed")
        self.assertFalse(await agg_repo.renew_lease(self.agg_id, agg_token, 60, self.now))
        self.assertFalse(await agg_repo.release_claim(self.agg_id, agg_token))
        self.assertFalse(await agg_repo.mark_failed(self.agg_id, agg_token, "synthetic", "synthetic", self.now))
        self.assertFalse(await agg_repo.save_result_and_complete(self.agg_id, agg_token, self.result(), self.now))
        self.assertNotEqual((await agg_repo.claim_explicit(self.agg_id, uuid4(), 60, self.now)).status.value, "claimed")
        async with self.sf() as s:
            repo = SqlAlchemySourceTaskRepository(s)
            token = source_claim.claim_token
            self.assertFalse(await repo.renew_lease(self.task_id, claim_token=token))
            self.assertFalse(await repo.complete_task_and_update_run(self.task_id, documents_count=1, claim_token=token))
            self.assertFalse(await repo.fail_task_and_update_run(self.task_id, claim_token=token, error_code="synthetic", error_message="synthetic"))
            self.assertFalse((await repo.claim_task_explicit(self.task_id)).is_claimed)
        self.assertEqual(await self.state(), before)
        self.assertEqual(await self.outbox_count(), intents)
        async with self.sf() as s:
            self.assertIsNone(await s.scalar(select(OrmSourceAnalysisResult.id).where(OrmSourceAnalysisResult.run_id == self.run_id)))
            self.assertIsNone(await s.scalar(select(OrmAggregatedAnalysisResult.id).where(OrmAggregatedAnalysisResult.run_id == self.run_id)))

    async def test_parent_only_cancel_fences_analysis_heartbeat_failure_and_result(self):
        async with self.sf() as s:
            claim = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        await self.parent_only_cancel()
        async with self.sf() as s:
            repo = SqlAlchemySourceAnalysisRepository(s)
            self.assertFalse(await repo.renew_lease(self.analysis_id, claim.claim_token))
            self.assertFalse(await repo.fail_analysis(self.analysis_id, claim.claim_token, "synthetic", "synthetic"))
            self.assertFalse(await SqlAlchemySourceAnalysisResultRepository(s).save_result_fenced(
                self.analysis_id, claim.claim_token, SourceAnalysisResultData("c"*64,"synthetic",[],{})))

    async def test_cas_mismatch_and_legacy_and_terminal_are_noops(self):
        before = await self.state()
        self.assertEqual((await self.cancel(expected_status=MonitoringRunStatus.PENDING)).status, "state_changed")
        self.assertEqual((await self.cancel(expected_updated_at=self.now-timedelta(days=1))).status, "state_changed")
        self.assertEqual(await self.state(), before)
        async with self.sf() as s, s.begin():
            legacy_id = uuid4()
            s.add(MonitoringRun(id=legacy_id, monitoring_job_id=self.job_id, execution_mode="legacy", status="completed", expected_sources=0))
        self.assertEqual((await provide_run_cancellation(self.sf).execute(legacy_id)).status,"not_distributed")
        self.assertEqual((await provide_run_cancellation(self.sf).execute(uuid4())).status,"not_found")

    async def test_tracker_state_policy_preserves_counters_and_timestamps(self):
        statuses = ["open", "ready_all_terminal", "ready_deadline", "aggregation_dispatched", "analysis_ready", "control_failed", "cancelled"]
        for tracker_status in statuses:
            with self.subTest(tracker_status=tracker_status):
                run_id = uuid4()
                async with self.sf() as s, s.begin():
                    # Separate synthetic parent and job, never one of the pinned IDs.
                    job = await s.get(MonitoringJob, self.job_id)
                    clone = MonitoringJob(topic="synthetic", requested_by=job.requested_by, bot_identity_id=job.bot_identity_id,
                        date_from_jalali=job.date_from_jalali, date_to_jalali=job.date_to_jalali,
                        date_from_gregorian=job.date_from_gregorian, date_to_gregorian=job.date_to_gregorian)
                    s.add(clone)
                    await s.flush()
                    s.add(MonitoringRun(id=run_id, monitoring_job_id=clone.id, status="pending", execution_mode="distributed", expected_sources=0))
                    await s.flush()
                    s.add(OrmRunCompletionTracker(run_id=run_id, status=tracker_status, ready_at=self.now,
                        analysis_ready_at=self.now, analysis_terminal_count=2, analysis_successful_count=1, analysis_failed_count=1))
                self.assertEqual((await provide_run_cancellation(self.sf).execute(run_id)).status,"cancelled")
                async with self.sf() as s:
                    row = await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
                    self.assertEqual(row.status, "control_failed" if tracker_status == "control_failed" else "cancelled")
                    self.assertEqual((row.analysis_terminal_count, row.analysis_failed_count, row.analysis_ready_at), (2,1,self.now))

    async def test_mixed_completed_failed_children_and_completed_result_manifest_preserved(self):
        async with self.sf() as s, s.begin():
            await s.execute(update(MonitoringRun).where(MonitoringRun.id == self.run_id).values(
                expected_sources=3, terminal_sources=2, successful_sources=1, failed_sources=1))
            for ordinal, status in [(2, "completed"), (3, "failed")]:
                src = Source(title="Synthetic", base_url=f"https://{uuid4().hex}.example.invalid", type=SourceType.NEWS_SITE,
                             created_by=(await s.get(Source,self.source_id)).created_by)
                s.add(src)
                await s.flush()
                t = SourceTask(run_id=self.run_id, source_id=src.id, ordinal=ordinal, status=status,
                               error_code="OLD_ERROR", attempt_count=4, documents_count=7, finished_at=self.now)
                s.add(t)
                await s.flush()
                a = OrmSourceAnalysis(source_task_id=t.id, run_id=self.run_id, source_id=src.id,
                    status=status, error_code="OLD_ERROR", attempt_count=4, input_digest="c"*64, finished_at=self.now)
                s.add(a)
                await s.flush()
                if status == "completed":
                    result = OrmSourceAnalysisResult(source_analysis_id=a.id, source_task_id=t.id, run_id=self.run_id,
                        source_id=src.id, summary="synthetic", findings=[], evidence_map={}, input_digest="c"*64)
                    s.add(result)
                    await s.flush()
                    s.add(OrmRunAggregationInput(aggregation_id=self.agg_id, source_analysis_result_id=result.id,
                        source_id=src.id, position=1, input_digest="c"*64))
            await s.execute(update(OrmRunAggregation).where(OrmRunAggregation.id == self.agg_id).values(
                status="completed", finished_at=self.now, claim_token=None, lease_expires_at=None))
            r = self.result()
            s.add(OrmAggregatedAnalysisResult(**{k:v for k,v in vars(r).items()}))
        async def history():
            async with self.sf() as s:
                data = []
                for model in (SourceTask, OrmSourceAnalysis, OrmSourceAnalysisResult, OrmRunAggregationInput, OrmAggregatedAnalysisResult, OrmRunAggregation):
                    if model in (SourceTask, OrmSourceAnalysis):
                        q = select(model.__table__).where(model.run_id == self.run_id, model.status.in_(["completed","failed"]))
                    elif model == OrmRunAggregationInput:
                        q = select(model.__table__).where(model.aggregation_id == self.agg_id)
                    else:
                        q = select(model.__table__).where(model.run_id == self.run_id)
                    data.append([dict(x) for x in (await s.execute(q)).mappings()])
                return data
        before = await history()
        await self.cancel()
        self.assertEqual(await history(), before)
        self.assertEqual((await self.state())["counters"], (3,1,1,1))

    async def test_mid_and_late_faults_roll_back_every_participant(self):
        await self.tracker()
        before = await self.state()
        intents = await self.outbox_count()
        base = SqlAlchemyRunCancellationTransaction(self.sf)
        for point in ("sources", "analyses", "aggregations", "before_commit"):
            with self.subTest(point=point):
                class FaultBoundary:
                    @asynccontextmanager
                    async def transaction(inner):
                        async with base.transaction() as p:
                            if point != "before_commit":
                                fake = AsyncMock()
                                fake.cancel_for_run.side_effect = RuntimeError("synthetic rollback")
                                p = replace(p, **{point:fake})
                            yield p
                            if point == "before_commit":
                                raise RuntimeError("synthetic late rollback")
                with self.assertRaises(RuntimeError):
                    await CancelDistributedMonitoringRun(FaultBoundary()).execute(self.run_id)
                self.assertEqual(await self.state(), before)
                self.assertEqual(await self.outbox_count(), intents)

    async def test_two_concurrent_cancellers(self):
        outcomes = await asyncio.wait_for(asyncio.gather(self.cancel(),self.cancel()),10)
        self.assertEqual(sorted(o.status for o in outcomes), ["already_terminal","cancelled"])
        self.assertEqual((await self.state())["counters"], (1,0,0,1))

    async def test_real_claim_vs_cancel_race(self):
        async def claim():
            async with self.sf() as s:
                return await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        await asyncio.wait_for(asyncio.gather(claim(),self.cancel()),10)
        after = await self.state()
        self.assertEqual(after["analysis"], "cancelled")
        self.assertIsNone(after["tokens"][1])

    async def test_real_completion_wins_before_cancel(self):
        claimed, release = asyncio.Event(), asyncio.Event()
        async with self.sf() as s:
            claim = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        async def finish():
            async with self.sf() as s:
                repo = SqlAlchemySourceAnalysisResultRepository(s)
                self.assertTrue(await repo.save_result_fenced(self.analysis_id, claim.claim_token,
                    SourceAnalysisResultData("c"*64,"synthetic",[],{})))
                claimed.set()
                await release.wait()
                self.assertTrue(await SqlAlchemySourceAnalysisRepository(s).complete_analysis(self.analysis_id, claim.claim_token))
        worker = asyncio.create_task(finish())
        await asyncio.wait_for(claimed.wait(),5)
        cancelling = asyncio.create_task(self.cancel())
        await asyncio.sleep(0.1)
        self.assertFalse(cancelling.done())
        release.set()
        await asyncio.wait_for(asyncio.gather(worker,cancelling),10)
        self.assertEqual((await self.state())["analysis"], "completed")
        async with self.sf() as s:
            self.assertIsNotNone(await s.scalar(select(OrmSourceAnalysisResult.id).where(OrmSourceAnalysisResult.run_id == self.run_id)))

    async def test_real_cancel_wins_before_completion_and_reclaim(self):
        async with self.sf() as s:
            claim = await SqlAlchemySourceAnalysisRepository(s).claim_explicit(self.analysis_id)
        closed, release = asyncio.Event(), asyncio.Event()
        base = SqlAlchemyRunCancellationTransaction(self.sf)
        class HoldBoundary:
            @asynccontextmanager
            async def transaction(inner):
                async with base.transaction() as p:
                    yield p
                    closed.set()
                    await release.wait()
        cancel = asyncio.create_task(CancelDistributedMonitoringRun(HoldBoundary()).execute(self.run_id))
        await asyncio.wait_for(closed.wait(),5)
        async def finish():
            async with self.sf() as s:
                return await SqlAlchemySourceAnalysisRepository(s).complete_analysis(self.analysis_id,claim.claim_token)
        worker = asyncio.create_task(finish())
        await asyncio.sleep(0.1)
        self.assertFalse(worker.done())
        release.set()
        _, accepted = await asyncio.wait_for(asyncio.gather(cancel,worker),10)
        self.assertFalse(accepted)
        self.assertEqual((await self.state())["analysis"], "cancelled")

    async def test_real_aggregation_completion_wins_before_cancel(self):
        import app.modules.analysis.infrastructure.run_aggregation_repository as module
        repo = SqlAlchemyRunAggregationRepository(self.sf)
        token = uuid4()
        await repo.claim_explicit(self.agg_id, token, 60, self.now)
        closed, release = asyncio.Event(), asyncio.Event()
        original = module.enqueue
        async def gated_enqueue(*args, **kwargs):
            await original(*args, **kwargs)
            closed.set()
            await release.wait()
        with patch.object(module, "enqueue", gated_enqueue):
            worker = asyncio.create_task(repo.save_result_and_complete(self.agg_id,token,self.result(),self.now))
            await asyncio.wait_for(closed.wait(),5)
            cancel = asyncio.create_task(self.cancel())
            await asyncio.sleep(0.1)
            self.assertFalse(cancel.done())
            release.set()
            accepted, _ = await asyncio.wait_for(asyncio.gather(worker,cancel),10)
        self.assertTrue(accepted)
        self.assertEqual((await self.state())["aggregation"], "completed")

    async def test_real_cancel_wins_against_aggregate_result_and_expired_reclaim(self):
        repo = SqlAlchemyRunAggregationRepository(self.sf)
        token = uuid4()
        await repo.claim_explicit(self.agg_id,token,60,self.now)
        closed, release = asyncio.Event(), asyncio.Event()
        base = SqlAlchemyRunCancellationTransaction(self.sf)
        class HoldBoundary:
            @asynccontextmanager
            async def transaction(inner):
                async with base.transaction() as p:
                    yield p
                    closed.set()
                    await release.wait()
        cancel = asyncio.create_task(CancelDistributedMonitoringRun(HoldBoundary()).execute(self.run_id))
        await asyncio.wait_for(closed.wait(),5)
        result = asyncio.create_task(repo.save_result_and_complete(self.agg_id,token,self.result(),self.now))
        claim = asyncio.create_task(repo.claim_explicit(self.agg_id,uuid4(),60,self.now+timedelta(hours=1)))
        await asyncio.sleep(0.1)
        self.assertFalse(result.done())
        self.assertFalse(claim.done())
        release.set()
        _, accepted, outcome = await asyncio.wait_for(asyncio.gather(cancel,result,claim),10)
        self.assertFalse(accepted)
        self.assertNotEqual(outcome.status.value,"claimed")
        self.assertEqual((await self.state())["tokens"][2],None)

    async def test_stale_source_persister_rejects_before_shared_documents_or_health(self):
        from app.modules.monitoring.infrastructure.source_result_persister import SourceResultPersister
        async with self.sf() as s:
            token = (await SqlAlchemySourceTaskRepository(s).claim_task_explicit(self.task_id)).claim_token
        await self.cancel()
        def forbidden(*args):
            raise AssertionError("shared/business repository reached after cancellation")
        recorder = AsyncMock()
        persister = SourceResultPersister(self.sf, forbidden, forbidden, forbidden, recorder)
        self.assertFalse(await persister.persist_success(self.task_id,token,self.job_id,self.source_id,None,None,None))
        self.assertFalse(await persister.persist_failure(self.task_id,token,self.job_id,self.source_id,RuntimeError("synthetic")))
        recorder.record_failure.assert_not_called()

    async def test_five_pinned_shapes_with_synthetic_ids_only(self):
        for shape in range(1,6):
            with self.subTest(shape=shape):
                rid, tid, aid, gid = [uuid4() for _ in range(4)]
                async with self.sf() as s, s.begin():
                    job = await s.get(MonitoringJob,self.job_id)
                    j = MonitoringJob(topic="synthetic shape",requested_by=job.requested_by,bot_identity_id=job.bot_identity_id,
                        date_from_jalali=job.date_from_jalali,date_to_jalali=job.date_to_jalali,
                        date_from_gregorian=job.date_from_gregorian,date_to_gregorian=job.date_to_gregorian)
                    s.add(j)
                    await s.flush()
                    s.add(MonitoringRun(id=rid,monitoring_job_id=j.id,execution_mode="distributed",
                        status="pending" if shape == 1 else "running", expected_sources=0 if shape==5 else 1,
                        terminal_sources=1 if shape in (2,3,4) else 0, successful_sources=1 if shape in (2,3,4) else 0))
                    await s.flush()
                    if shape != 5:
                        s.add(SourceTask(id=tid,run_id=rid,source_id=self.source_id,ordinal=1,
                            status="pending" if shape==1 else "completed",finished_at=None if shape==1 else self.now))
                        await s.flush()
                    if shape in (2,4):
                        s.add(OrmSourceAnalysis(id=aid,source_task_id=tid,run_id=rid,source_id=self.source_id,
                            status="running" if shape==2 else "completed",claim_token=uuid4() if shape==2 else None,
                            lease_expires_at=self.now+timedelta(minutes=5) if shape==2 else None,attempt_count=3,
                            input_digest="c"*64,finished_at=self.now if shape==4 else None))
                        await s.flush()
                    if shape in (3,4):
                        s.add(OrmRunAggregation(id=gid,run_id=rid,status="running" if shape==3 else "completed",
                            claim_token=uuid4() if shape==3 else None,lease_expires_at=self.now-timedelta(minutes=5) if shape==3 else None,
                            attempt_count=2,readiness_reason="all_terminal",manifest_digest="a"*64,run_analysis_spec_digest="b"*64))
                        await s.flush()
                    if shape == 4:
                        result = OrmSourceAnalysisResult(source_analysis_id=aid,source_task_id=tid,run_id=rid,
                            source_id=self.source_id,input_digest="c"*64,summary="synthetic",findings=[],evidence_map={})
                        s.add(result)
                        await s.flush()
                        s.add(OrmRunAggregationInput(aggregation_id=gid,source_analysis_result_id=result.id,
                            source_id=self.source_id,position=1,input_digest="c"*64))
                        r = replace(self.result(),aggregation_id=gid,run_id=rid)
                        s.add(OrmAggregatedAnalysisResult(**vars(r)))
                    if shape in (3,5):
                        s.add(OrmRunCompletionTracker(run_id=rid,status="aggregation_dispatched" if shape==3 else "open",
                            aggregation_id=gid if shape==3 else None,ready_at=self.now))
                before = await self.outbox_count()
                outcome = await provide_run_cancellation(self.sf).execute(rid)
                self.assertEqual(outcome.status,"cancelled")
                self.assertEqual(await self.outbox_count(),before)
                async with self.sf() as s:
                    self.assertEqual((await s.get(MonitoringRun,rid)).status.value,"cancelled")
                    if shape==1:
                        self.assertEqual((await s.get(SourceTask,tid)).status.value,"cancelled")
                    if shape==2:
                        self.assertEqual((await s.get(OrmSourceAnalysis,aid)).status.value,"cancelled")
                    if shape==3:
                        self.assertEqual((await s.get(OrmRunAggregation,gid)).status,"cancelled")
                    if shape==4:
                        self.assertEqual((await s.get(SourceTask,tid)).status.value,"completed")
                        self.assertEqual((await s.get(OrmSourceAnalysis,aid)).status.value,"completed")
                        self.assertEqual((await s.get(OrmRunAggregation,gid)).status,"completed")
                        self.assertEqual(await s.scalar(select(func.count()).select_from(OrmAggregatedAnalysisResult).where(OrmAggregatedAnalysisResult.run_id==rid)),1)
                        self.assertEqual(await s.scalar(select(OrmRunAggregationInput.input_digest).where(OrmRunAggregationInput.aggregation_id==gid)),"c"*64)
                    if shape==5:
                        tracker = await s.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id==rid))
                        self.assertEqual(tracker.status,"cancelled")

    async def test_stale_handoffs_and_control_events_cannot_create_missing_work(self):
        from app.modules.analysis.infrastructure.handoff_adapter import AnalysisHandoffAdapter
        from app.modules.monitoring.infrastructure.completion_tracker_repository import SqlAlchemyRunCompletionTrackerRepository
        from app.modules.monitoring.infrastructure.celery_dispatcher import OutboxSourceTaskDispatcher
        await self.cancel()
        intents = await self.outbox_count()
        await AnalysisHandoffAdapter(self.sf).ensure_and_dispatch(self.task_id)
        self.assertIsNone(await SqlAlchemyRunAggregationRepository(self.sf).ensure_aggregation_and_manifest(self.run_id,"all_terminal",None,{}))
        async with self.sf() as s:
            await OutboxSourceTaskDispatcher(s).dispatch(self.task_id)
            await s.commit()
        tracker = SqlAlchemyRunCompletionTrackerRepository(self.sf)
        self.assertIsNone(await tracker.get_or_create(self.run_id,self.now))
        new, missing = await tracker.record_event_and_update_counts(self.run_id,"analysis_terminal",self.analysis_id,"completed",self.now)
        self.assertFalse(new)
        self.assertIsNone(missing)
        self.assertEqual(await self.outbox_count(),intents)
        self.assertIsNone((await self.state())["tracker"])
        async with self.sf() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(OrmRunCompletionEvent).where(OrmRunCompletionEvent.run_id==self.run_id)),0)

    async def test_real_redis_queued_and_duplicate_messages_ack_without_business_execution(self):
        from app.core.config import settings
        assert_isolated(settings.database_url,settings.celery_broker_url)
        self.assertEqual(settings.redis_url,os.environ["PHASE8A3_REDIS_URL"])
        from redis import Redis
        from celery.contrib.testing.worker import start_worker
        from app.workers.celery_app import celery_app
        from app.shared.integration.outbox import enqueue
        from app.modules.monitoring.infrastructure.source_execution_engine import SourceExecutionEngine
        from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
        from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
        broker = Redis.from_url(os.environ["PHASE8A3_REDIS_URL"])
        # Private queues prevent the regression suite's normal source worker
        # from consuming these deliberately held pre-cancellation messages.
        queues = tuple(f"phase8a3-{self.run_id}.{kind}" for kind in ("source","analysis","aggregate"))
        for q in queues:
            self.assertEqual(broker.llen(q),0)
        tasks = [
            ("monitoring.process_source_task",self.task_id,queues[0],"SOURCE_EXECUTION"),
            ("analysis.process_source_analysis",self.analysis_id,queues[1],"SOURCE_ANALYSIS"),
            ("analysis.aggregate_run",self.agg_id,queues[2],"RUN_AGGREGATION"),
        ]
        results, outbox_ids = [], []
        async with self.sf() as s, s.begin():
            for name, identifier, queue, kind in tasks:
                key = f"synthetic-broker:{identifier}"
                field = {"SOURCE_EXECUTION":"source_task_id", "SOURCE_ANALYSIS":"source_analysis_id", "RUN_AGGREGATION":"aggregation_id"}[kind]
                await enqueue(s,message_key=key,message_type=kind,
                    payload={"schema_version":1,field:str(identifier)})
                oid = await s.scalar(select(IntegrationOutbox.id).where(IntegrationOutbox.message_key==key))
                outbox_ids.append(oid)
        # Real serialized Celery messages are waiting in Redis BEFORE cancellation.
        for (name,identifier,queue,_), oid in zip(tasks,outbox_ids):
            for duplicate in range(2):
                results.append(celery_app.send_task(name,args=[str(identifier)],queue=queue,headers={"outbox_id":str(oid)}))
        self.assertEqual(sum(broker.llen(q) for q in queues),6)
        await self.cancel()
        before, intents = await self.state(), await self.outbox_count()
        crawl, analyze, aggregate = AsyncMock(), AsyncMock(), AsyncMock()
        def consume():
            with patch.object(SourceExecutionEngine,"execute_crawl",crawl), \
                 patch.object(SingleSourceAnalyzer,"analyze_source",analyze), \
                 patch.object(RunAggregatorAdapter,"aggregate",aggregate):
                with start_worker(celery_app,pool="solo",concurrency=1,queues=queues,
                                  perform_ping_check=False,shutdown_timeout=10):
                    return [r.get(timeout=15) for r in results]
        responses = await asyncio.wait_for(asyncio.to_thread(consume),45)
        self.assertTrue(all(r["status"]=="run_cancelled" for r in responses))
        crawl.assert_not_called()
        analyze.assert_not_called()
        aggregate.assert_not_called()
        self.assertEqual(await self.state(),before)
        self.assertEqual(await self.outbox_count(),intents)
        async with self.sf() as s:
            for oid in outbox_ids:
                self.assertIsNotNone((await s.get(IntegrationOutbox,oid)).consumer_acked_at)
        self.assertEqual(sum(broker.llen(q) for q in queues),0)
        self.assertEqual(broker.hlen("unacked"),0)
        broker.close()

    async def test_stale_analysis_failure_never_publishes_optional_terminal_signal(self):
        from app.modules.analysis.application.source_analysis_use_cases import ExecuteSourceAnalysisUseCase
        async def fail_after_cancel(**kwargs):
            await self.cancel()
            raise RuntimeError("synthetic late failure")
        analyze, documents, publisher = AsyncMock(), AsyncMock(), Mock()
        analyze.execute.side_effect = fail_after_cancel
        documents.get_documents_for_task.return_value=[]
        intents=await self.outbox_count()
        async with self.sf() as s:
            usecase=ExecuteSourceAnalysisUseCase(SqlAlchemySourceAnalysisRepository(s),
                SqlAlchemySourceAnalysisResultRepository(s),documents,analyze,terminal_publisher=publisher)
            await usecase.execute(self.analysis_id)
        publisher.publish_source_analysis_terminal.assert_not_called()
        self.assertEqual((await self.state())["analysis"],"cancelled")
        self.assertEqual(await self.outbox_count(),intents)

    async def test_stale_source_failure_never_publishes_optional_terminal_signal(self):
        from app.modules.monitoring.application.execute_source_task import ExecuteSourceTaskUseCase
        from app.modules.monitoring.infrastructure.source_result_persister import SourceResultPersister
        async with self.sf() as s, s.begin():
            spec=SourceExecutionSpec(self.source_id,"synthetic",None,None,{},"https://example.invalid","synthetic")
            await s.execute(update(SourceTask).where(SourceTask.id==self.task_id).values(execution_spec=spec.to_dict()))
        async def fail_after_cancel(*args):
            await self.cancel()
            raise RuntimeError("synthetic late failure")
        engine, publisher, recorder=AsyncMock(),Mock(),AsyncMock()
        engine.execute_crawl.side_effect=fail_after_cancel
        def forbidden(*args):
            raise AssertionError("Business persistence reached after cancellation")
        persister=SourceResultPersister(self.sf,forbidden,forbidden,forbidden,recorder)
        intents=await self.outbox_count()
        async with self.sf() as s:
            usecase=ExecuteSourceTaskUseCase(SqlAlchemySourceTaskRepository(s),
                SqlAlchemyMonitoringRunRepository(s),engine,persister,terminal_publisher=publisher)
            await usecase.execute(self.task_id)
        publisher.publish_source_terminal.assert_not_called()
        recorder.record_failure.assert_not_called()
        self.assertEqual((await self.state())["task"],"cancelled")
        self.assertEqual(await self.outbox_count(),intents)
