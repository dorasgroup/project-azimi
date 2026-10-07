"""Phase 6A: Analysis Runtime & Ownership Contract Closure Test Suite."""

from __future__ import annotations

import ast
import inspect
import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.models.bot_identity import BotIdentity
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_analysis import OrmSourceAnalysis, OrmSourceAnalysisResult
from app.models.source_task import SourceTask as OrmSourceTask
from app.models.source_task_document import SourceTaskDocument
from app.models.user import User, UserRole
from app.modules.analysis.application.source_analysis_use_cases import (
    AnalyzeSourceUseCase,
    ExecuteSourceAnalysisUseCase,
)
from app.modules.analysis.domain.source_analysis import (
    SOURCE_ANALYSIS_HEARTBEAT_SECONDS,
    SOURCE_ANALYSIS_LEASE_SECONDS,
    AnalysisClaimStatus,
    PermanentAnalysisError,
    SourceAnalysisResultData,
    SourceAnalysisSpec,
    SourceAnalysisStatus,
    TransientAnalysisError,
    classify_analysis_error,
    compute_analysis_input_digest,
)
from app.modules.analysis.infrastructure.handoff_adapter import AnalysisHandoffAdapter
from app.modules.analysis.infrastructure.source_analysis_repository import (
    SqlAlchemySourceAnalysisRepository,
)
from app.modules.analysis.infrastructure.source_analysis_result_repository import (
    SqlAlchemySourceAnalysisResultRepository,
    SqlAlchemySourceTaskDocumentRepository,
)
from app.modules.analysis.infrastructure.spec_resolver import SourceAnalysisSpecResolver
from app.modules.analysis.ports.source_analysis import (
    SourceAnalysisDispatcherPort,
    SourceTaskDocumentRecord,
)
from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.documents.infrastructure import SqlAlchemyDocumentRepository
from app.modules.documents.infrastructure.record_mapper import to_prepared_document_record
from app.modules.monitoring.application.execute_source_task import (
    ExecuteSourceTaskUseCase,
)
from app.modules.monitoring.application.source_failure import SourceFailureRecorder
from app.modules.monitoring.domain.execution import (
    ClaimStatus,
    MonitoringRunStatus,
    SourceExecutionSpec,
    SourceTaskStatus,
)
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
    SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.infrastructure.pipeline_adapters import (
    SqlAlchemyPipelinePersistenceAdapter,
)
from app.modules.monitoring.infrastructure.repository import (
    SqlAlchemyMonitoringJobRepository,
)
from app.modules.monitoring.infrastructure.source_result_persister import (
    SourceResultPersister,
)
from app.modules.monitoring.ports.execution import SourceExecutionEnginePort
from app.processing.schemas import PreparedDocument
from app.shared.integration.relay import OutboxRelay


class MockAnalysisDispatcher(SourceAnalysisDispatcherPort):
    def __init__(self) -> None:
        self.dispatched_ids: list[UUID] = []

    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str:
        self.dispatched_ids.append(source_analysis_id)
        return f"mock-analysis-{source_analysis_id}"


class FailingAnalysisDispatcher(SourceAnalysisDispatcherPort):
    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str:
        raise RuntimeError("Celery broker connection failed during analysis dispatch")


class FakeControlledEngine(SourceExecutionEnginePort):
    def __init__(self, doc_titles: list[str]) -> None:
        self.doc_titles = doc_titles

    async def execute_crawl(self, spec: SourceExecutionSpec):
        class DummyDiag:
            status = "completed"
            reason_code = "ok"
            summary = "done"
            discovered_total = len(self.doc_titles)
            selected_total = len(self.doc_titles)
            fetched_total = len(self.doc_titles)
            successful_pages = len(self.doc_titles)
            provider_counts = {}
            selected_provider_counts = {}

            def model_dump(self, mode="json"):
                return {"status": "completed"}

        class DummyProbe:
            success = True
            robots_allowed = True
            http_status = 200
            reason = "OK"
            response_time_ms = 50

        class DummyCrawlResult:
            pages = [f"page_{i}" for i in range(len(self.doc_titles))]
            discovered_links = []
            diagnostics = DummyDiag()
            probe = DummyProbe()
            finished_at = datetime.now(UTC)

        docs = []
        for i, title in enumerate(self.doc_titles):
            doc_url = f"https://example.org/p/{uuid4().hex[:8]}_{i}"
            docs.append(
                PreparedDocument(
                    url=doc_url,
                    canonical_url=doc_url,
                    title=title,
                    clean_text=f"Sample text content for {title} discussing monitoring.",
                    markdown=f"# {title}\nClean text",
                    author="Author",
                    source_name="Source",
                    published_at=datetime(2026, 1, 2, tzinfo=UTC),
                    published_at_jalali="1404/10/12",
                    language="fa",
                    lead_image_url=None,
                    matched_score=0.9,
                    matched_terms=["monitoring"],
                    content_hash=f"hash_{title}_{i}",
                    published_at_source="2026-01-02",
                    published_at_confidence=1.0,
                    published_at_raw="2026-01-02",
                    metadata={},
                )
            )

        class DummyPreparedBatch:
            documents = docs
            rejected = []
            duplicate_count = 0

        class DummyCrawlPolicy:
            max_pages = len(self.doc_titles)

        return DummyCrawlResult(), DummyPreparedBatch(), DummyCrawlPolicy()


class FlakyAnalyzer:
    """Analyzer that fails with transient error on first attempt, then succeeds."""

    def __init__(self, transient_attempts: int = 1) -> None:
        self.transient_attempts = transient_attempts
        self.attempt_call_count = 0

    async def analyze_source(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData:
        self.attempt_call_count += 1
        if self.attempt_call_count <= self.transient_attempts:
            raise TransientAnalysisError("HTTP 503 Service Unavailable from AI gateway")

        digest = compute_analysis_input_digest(
            source_id=documents[0].source_id if documents else uuid4(),
            topic=topic,
            spec=spec,
            documents=documents,
        )
        return SourceAnalysisResultData(
            schema_version="1.0",
            input_digest=digest,
            summary="Flaky analyzer succeeded on retry",
            findings=[{"title": "Key Insight", "explanation": "Success", "evidence": "Doc evidence", "document_url": "https://example.org"}],
            evidence_map={"https://example.org": "Evidence text"},
            raw_result={"status": "ok", "retry_succeeded": True},
        )


class SlowAnalyzer:
    """Analyzer that takes time to complete, demonstrating heartbeat lease renewals."""

    def __init__(self, delay_seconds: float = 0.15) -> None:
        self.delay_seconds = delay_seconds

    async def analyze_source(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData:
        await asyncio.sleep(self.delay_seconds)
        digest = compute_analysis_input_digest(
            source_id=documents[0].source_id if documents else uuid4(),
            topic=topic,
            spec=spec,
            documents=documents,
        )
        return SourceAnalysisResultData(
            schema_version="1.0",
            input_digest=digest,
            summary="Slow analyzer finished with heartbeats",
            findings=[],
            evidence_map={},
            raw_result={"status": "ok"},
        )


@pytest.mark.asyncio
class TestPhase6AContractClosure(IsolatedAsyncioTestCase):
    """Rigorous contract closure verification for Phase 6A."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _setup_environment(self, session: AsyncSession):
        user = await session.scalar(select(User).limit(1))
        if not user:
            user = User(
                id=uuid4(),
                email=f"p6a-{uuid4().hex[:6]}@example.org",
                role=UserRole.ADMIN,
                password_hash="fake",
                full_name="Phase 6A Tester",
                is_active=True,
            )
            session.add(user)
            await session.flush()

        bot = await session.scalar(select(BotIdentity).limit(1))
        bot_id = bot.id if bot else uuid4()

        job = MonitoringJob(
            id=uuid4(),
            topic="p6a contract topic",
            date_from_jalali="1405/01/01",
            date_to_jalali="1405/01/10",
            date_from_gregorian=date(2026, 1, 1),
            date_to_gregorian=date(2026, 1, 10),
            requested_by=user.id,
            bot_identity_id=bot_id,
            status=JobStatus.CRAWLING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(job)
        await session.flush()

        run = OrmMonitoringRun(
            id=uuid4(),
            monitoring_job_id=job.id,
            execution_mode="distributed",
            status=MonitoringRunStatus.RUNNING,
            expected_sources=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()

        source = Source(
            id=uuid4(),
            title=f"Source {uuid4().hex[:6]}",
            base_url=f"https://source-{uuid4().hex[:6]}.example.org",
            type=SourceType.NEWS_SITE,
            last_status=SourceStatus.SUCCESS,
            created_by=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(source)
        await session.flush()

        task = OrmSourceTask(
            id=uuid4(),
            run_id=run.id,
            source_id=source.id,
            ordinal=1,
            status=SourceTaskStatus.PENDING,
            attempt_count=0,
            execution_spec={
                "source_id": str(source.id),
                "topic": job.topic,
                "date_from": "2026-01-01",
                "date_to": "2026-01-10",
                "crawl_config": {},
                "base_url": source.base_url,
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()
        return job, run, source, task

    async def _assert_durable_analysis_handoff(
        self, session: AsyncSession, source_task_id: UUID,
        *, expect_source_commit: bool = True,
    ) -> UUID:
        analysis = await session.scalar(select(OrmSourceAnalysis).where(
            OrmSourceAnalysis.source_task_id == source_task_id))
        self.assertIsNotNone(analysis)
        successor = await session.scalar(select(IntegrationOutbox).where(
            IntegrationOutbox.message_key == f"source-analysis:{source_task_id}"))
        self.assertIsNotNone(successor)
        self.assertEqual(successor.message_type, "SOURCE_ANALYSIS")
        self.assertEqual(successor.payload_json["source_analysis_id"], str(analysis.id))
        if expect_source_commit:
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis-handoff:{source_task_id}"))
            self.assertIsNotNone(parent)
            self.assertEqual(parent.message_type, "SOURCE_ANALYSIS_HANDOFF")
        return analysis.id

    # 1. Heartbeat Renewal During Slow Provider Call
    async def test_slow_provider_heartbeat_renewal_and_active_lease_protection(self) -> None:
        """Heartbeat extends lease multiple times during long provider call; other workers see ACTIVE_LEASE."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc 1", "Doc 2"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            dispatcher = MockAnalysisDispatcher()
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            await use_case.execute(task.id)

            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            result_repo = SqlAlchemySourceAnalysisResultRepository(session)
            doc_repo = SqlAlchemySourceTaskDocumentRepository(session)

            # Use slow analyzer (0.15s) with rapid heartbeat (0.03s)
            slow_analyzer = SlowAnalyzer(delay_seconds=0.15)
            analysis_uc = ExecuteSourceAnalysisUseCase(
                analysis_repository=analysis_repo,
                result_repository=result_repo,
                document_reader=doc_repo,
                analyze_use_case=AnalyzeSourceUseCase(slow_analyzer),
                heartbeat_seconds=0.03,
            )

            # Run slow analysis and concurrently check claim fencing
            async def _concurrent_claim_checker():
                await asyncio.sleep(0.06)  # Wait for first heartbeat to have run
                async with SessionFactory() as check_session:
                    check_repo = SqlAlchemySourceAnalysisRepository(check_session)
                    # Attempt to claim while worker 1 is running
                    outcome = await check_repo.claim_explicit(analysis_id)
                    self.assertEqual(outcome.status, AnalysisClaimStatus.ACTIVE_LEASE)
                    self.assertGreater(outcome.lease_expires_at, datetime.now(UTC))

            task1 = asyncio.create_task(analysis_uc.execute(analysis_id))
            task2 = asyncio.create_task(_concurrent_claim_checker())
            res1, _ = await asyncio.gather(task1, task2)

            self.assertEqual(res1["status"], "completed")

            # Verify final state in DB
            completed_row = await analysis_repo.get_by_id(analysis_id)
            self.assertIsNotNone(completed_row)
            self.assertEqual(completed_row.status, SourceAnalysisStatus.COMPLETED)
            self.assertIsNotNone(completed_row.input_digest)

    # 2. Transient Failure State Machine: Release Claim & Clean Retry
    async def test_transient_failure_state_machine_clean_retry(self) -> None:
        """On transient error, claim is released to PENDING; Celery retry claims cleanly without ACTIVE_LEASE deadlock."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Transient"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            dispatcher = MockAnalysisDispatcher()
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            await use_case.execute(task.id)

            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            result_repo = SqlAlchemySourceAnalysisResultRepository(session)
            doc_repo = SqlAlchemySourceTaskDocumentRepository(session)

            flaky = FlakyAnalyzer(transient_attempts=1)
            analysis_uc = ExecuteSourceAnalysisUseCase(
                analysis_repository=analysis_repo,
                result_repository=result_repo,
                document_reader=doc_repo,
                analyze_use_case=AnalyzeSourceUseCase(flaky),
            )

            # Attempt 1: Fails transiently
            with self.assertRaises(TransientAnalysisError):
                await analysis_uc.execute(analysis_id)

            # Invariant: Claim MUST be released to PENDING with claim_token = None
            async with SessionFactory() as verify_sess:
                check_repo = SqlAlchemySourceAnalysisRepository(verify_sess)
                row_after_fail = await check_repo.get_by_id(analysis_id)
                self.assertIsNotNone(row_after_fail)
                self.assertEqual(row_after_fail.status, SourceAnalysisStatus.PENDING)
                self.assertIsNone(row_after_fail.claim_token)
                self.assertIsNone(row_after_fail.lease_expires_at)
                self.assertEqual(row_after_fail.attempt_count, 1)

            # Attempt 2: Celery retry delivery. Should claim cleanly without ACTIVE_LEASE!
            res2 = await analysis_uc.execute(analysis_id)
            self.assertEqual(res2["status"], "completed")

            async with SessionFactory() as final_sess:
                final_repo = SqlAlchemySourceAnalysisRepository(final_sess)
                final_res_repo = SqlAlchemySourceAnalysisResultRepository(final_sess)
                final_row = await final_repo.get_by_id(analysis_id)
                self.assertIsNotNone(final_row)
                self.assertEqual(final_row.status, SourceAnalysisStatus.COMPLETED)
                self.assertEqual(final_row.attempt_count, 2)

                # Persisted result exactly once
                saved_res = await final_res_repo.get_by_analysis_id(analysis_id)
                self.assertIsNotNone(saved_res)
                self.assertEqual(saved_res.summary, "Flaky analyzer succeeded on retry")

    # 3. Permanent Failure Semantics
    async def test_permanent_failure_terminates_without_celery_retry(self) -> None:
        """On permanent error, analysis transitions to FAILED, returns failed status, no re-raise, run remains RUNNING."""
        class FatalAnalyzer:
            async def analyze_source(self, spec, docs, topic):
                raise PermanentAnalysisError("Irrecoverable schema violation in provider output")

        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Fatal"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            dispatcher = MockAnalysisDispatcher()
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            await use_case.execute(task.id)

            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            result_repo = SqlAlchemySourceAnalysisResultRepository(session)
            doc_repo = SqlAlchemySourceTaskDocumentRepository(session)

            fatal_analyzer = FatalAnalyzer()
            analysis_uc = ExecuteSourceAnalysisUseCase(
                analysis_repository=analysis_repo,
                result_repository=result_repo,
                document_reader=doc_repo,
                analyze_use_case=AnalyzeSourceUseCase(fatal_analyzer),
            )

            # Execution returns failure dictionary, does NOT re-raise
            res = await analysis_uc.execute(analysis_id)
            self.assertEqual(res["status"], "failed")

            # Check DB state
            row = await analysis_repo.get_by_id(analysis_id)
            self.assertIsNotNone(row)
            self.assertEqual(row.status, SourceAnalysisStatus.FAILED)
            self.assertEqual(row.error_code, "PermanentAnalysisError")

            # Check MonitoringRun status: MUST REMAIN RUNNING (no fan-in, no phase 7 finalization)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            current_run = await run_repo.get_by_id(run.id)
            self.assertEqual(current_run.status, MonitoringRunStatus.RUNNING)

    # 4. Worker Crash / Redelivery After Lease Expiry
    async def test_worker_crash_redelivery_after_lease_expiry(self) -> None:
        """Crashed worker holding active lease is reclaimed only after lease expiry, incrementing attempt count."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Crash"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            dispatcher = MockAnalysisDispatcher()
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            await use_case.execute(task.id)

            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)

            # Worker 1 claims analysis
            claim1 = await analysis_repo.claim_explicit(analysis_id, lease_seconds=300)
            self.assertEqual(claim1.status, AnalysisClaimStatus.CLAIMED)
            self.assertEqual(claim1.attempt_count, 1)

            # Immediate second delivery: rejected because lease is active
            claim_premature = await analysis_repo.claim_explicit(analysis_id)
            self.assertEqual(claim_premature.status, AnalysisClaimStatus.ACTIVE_LEASE)

            # Worker 1 dies without heartbeat. Simulate lease expiry by rewinding lease_expires_at
            expired_time = datetime.now(UTC) - timedelta(seconds=10)
            await session.execute(
                text("UPDATE source_analyses SET lease_expires_at = :exp WHERE id = :id"),
                {"exp": expired_time, "id": analysis_id},
            )
            await session.commit()

            # Worker 2 redelivery: lease is expired, successfully reclaims
            claim2 = await analysis_repo.claim_explicit(analysis_id, lease_seconds=300)
            self.assertEqual(claim2.status, AnalysisClaimStatus.CLAIMED)
            self.assertEqual(claim2.attempt_count, 2)
            self.assertNotEqual(claim1.claim_token, claim2.claim_token)

    # 5. Clean Ownership: SourceResultPersister Responsibility
    async def test_source_result_persister_clean_ownership(self) -> None:
        """SourceResultPersister persists crawl results, snapshots documents, but writes zero analysis tables."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Owned 1", "Doc Owned 2"])

            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )

            # Claim task so persister fence accepts it
            task_repo = SqlAlchemySourceTaskRepository(session)
            claim = await task_repo.claim_task_explicit(task.id)
            self.assertTrue(claim.is_claimed)

            crawl_res, batch, policy = await engine_inst.execute_crawl(task.execution_spec)
            persisted = await persister.persist_success(
                task_id=task.id,
                claim_token=claim.claim_token,
                job_id=job.id,
                source_id=source.id,
                crawl_result=crawl_res,
                prepared_batch=batch,
                crawl_policy=policy,
            )
            self.assertTrue(persisted)

            # Verify snapshot documents written
            snap_docs = (
                await session.execute(
                    select(SourceTaskDocument).where(SourceTaskDocument.source_task_id == task.id)
                )
            ).scalars().all()
            self.assertEqual(len(snap_docs), 2)

            # Verify ZERO rows in source_analyses and source_analysis_results
            analyses_count = (
                await session.execute(
                    select(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id == task.id)
                )
            ).scalars().all()
            self.assertEqual(len(analyses_count), 0, "SourceResultPersister must NOT touch source_analyses table")

            results_count = (
                await session.execute(
                    select(OrmSourceAnalysisResult).join(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id == task.id)
                )
            ).scalars().all()
            self.assertEqual(len(results_count), 0, "SourceResultPersister must NOT touch source_analysis_results table")

    # 6. Cross-Context Handoff Port & Adapter
    async def test_cross_context_handoff_port_and_adapter(self) -> None:
        """AnalysisHandoffAdapter idempotently provisions SourceAnalysis and publishes Celery message."""
        dispatcher = MockAnalysisDispatcher()
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )

            await handoff.ensure_and_dispatch(
                source_task_id=task.id,
                run_id=run.id,
                source_id=source.id,
                topic="Handoff Test Topic",
            )

            analysis_id = await self._assert_durable_analysis_handoff(
                session, task.id, expect_source_commit=False)
            self.assertEqual(dispatcher.dispatched_ids, [])

            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            row = await analysis_repo.get_by_id(analysis_id)
            self.assertIsNotNone(row)
            self.assertEqual(row.status, SourceAnalysisStatus.PENDING)
            self.assertEqual(row.source_task_id, task.id)
            # Immutable source task context wins over later delivery arguments.
            self.assertEqual(row.analysis_spec.topic, task.execution_spec["topic"])

    # 7. Handoff Idempotency
    async def test_handoff_idempotency_multiple_dispatches(self) -> None:
        """Calling ensure_and_dispatch multiple times results in exactly ONE DB row."""
        dispatcher = MockAnalysisDispatcher()
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )

            # Call 3 times
            await handoff.ensure_and_dispatch(source_task_id=task.id, run_id=run.id, source_id=source.id)
            await handoff.ensure_and_dispatch(source_task_id=task.id, run_id=run.id, source_id=source.id)
            await handoff.ensure_and_dispatch(source_task_id=task.id, run_id=run.id, source_id=source.id)

            rows = (
                await session.execute(
                    select(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id == task.id)
                )
            ).scalars().all()
            self.assertEqual(len(rows), 1, "Exactly one SourceAnalysis entity must exist for task")

    # 8. Handoff Crash Recovery: Already Terminal Task
    async def test_handoff_crash_recovery_already_terminal(self) -> None:
        """Source commit leaves an outbox obligation; duplicate source delivery is harmless."""
        dispatcher = MockAnalysisDispatcher()
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Crash Handoff"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )

            # Step 1: Run with analysis_handoff=None (simulates crash immediately after source commit)
            use_case1 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=None,
            )
            res1 = await use_case1.execute(task.id)
            self.assertEqual(res1["status"], "completed")
            self.assertEqual(len(dispatcher.dispatched_ids), 0)
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis-handoff:{task.id}"))
            self.assertIsNotNone(parent)
            self.assertIsNone(await session.scalar(select(OrmSourceAnalysis).where(
                OrmSourceAnalysis.source_task_id == task.id)))

            # The relay can consume the durable parent without recrawling.
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            await handoff.ensure_and_dispatch(task.id, consumed_outbox_id=parent.id)
            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            await session.refresh(parent)
            self.assertIsNotNone(parent.consumer_acked_at)

            # A duplicate source delivery cannot create a second logical analysis.
            use_case2 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            res2 = await use_case2.execute(task.id)
            self.assertEqual(res2["status"], "already_terminal")
            rows = (await session.execute(select(OrmSourceAnalysis).where(
                OrmSourceAnalysis.source_task_id == task.id))).scalars().all()
            self.assertEqual([row.id for row in rows], [analysis_id])

    # 9. Handoff Broker Failure Resilience
    async def test_handoff_broker_failure_resilience(self) -> None:
        """Broker outage cannot roll back source completion or lose durable handoff."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Broker Failure"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            use_case1 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=None,
            )
            res1 = await use_case1.execute(task.id)
            self.assertEqual(res1["status"], "completed")
            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis-handoff:{task.id}"))
            self.assertIsNotNone(parent)

            broken_broker = Mock()
            broken_broker.send_task.side_effect = RuntimeError("broker unavailable")
            with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
                await OutboxRelay(publisher=broken_broker)._publish(parent)
            self.assertIsNone(parent.consumer_acked_at)

            restored_broker = Mock()
            await OutboxRelay(publisher=restored_broker)._publish(parent)
            restored_broker.send_task.assert_called_once()
            handoff = AnalysisHandoffAdapter(SessionFactory)
            await handoff.ensure_and_dispatch(task.id, consumed_outbox_id=parent.id)
            await self._assert_durable_analysis_handoff(session, task.id)
            await session.refresh(parent)
            self.assertIsNotNone(parent.consumer_acked_at)

    # 10. Architecture Ownership Guardrails (AST Test)
    def test_architecture_ownership_guardrails_ast(self) -> None:
        """Static AST verification of architectural boundaries and ownership."""
        backend_dir = Path("/opt/rasad10/backend/app")

        # 1. monitoring.infrastructure must NOT import analysis.infrastructure
        mon_infra_dir = backend_dir / "modules" / "monitoring" / "infrastructure"
        for py_file in mon_infra_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("app.modules.analysis.infrastructure"),
                        f"Forbidden import '{node.module}' in {py_file}",
                    )
                    self.assertFalse(
                        "OrmSourceAnalysis" in [a.name for a in node.names],
                        f"Forbidden OrmSourceAnalysis import in {py_file}",
                    )

        # 2. monitoring.domain must NOT import analysis.*
        mon_domain_dir = backend_dir / "modules" / "monitoring" / "domain"
        for py_file in mon_domain_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("app.modules.analysis"),
                        f"Forbidden cross-domain import '{node.module}' in {py_file}",
                    )

        # 3. analysis.domain must NOT import monitoring.*
        ana_domain_dir = backend_dir / "modules" / "analysis" / "domain"
        for py_file in ana_domain_dir.rglob("*.py"):
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("app.modules.monitoring"),
                        f"Forbidden cross-domain import '{node.module}' in {py_file}",
                    )

    # 11. SourceAnalysisSpec Resolved from Runtime Settings & Custom Override
    def test_source_analysis_spec_resolved_from_runtime_settings(self) -> None:
        """Spec resolver snapshots active platform provider or custom override; zero hardcodes."""
        resolver = SourceAnalysisSpecResolver()

        # 1. Resolve from runtime platform settings (in prod: gapgpt / gapgpt-qwen-3.6)
        with patch(
            "app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider",
            return_value=SimpleNamespace(provider_id="gapgpt", model="fixture-model"),
        ) as active_provider:
            spec = resolver.resolve_spec(
                topic="Artificial Intelligence",
                date_range="2026-01-01 to 2026-01-10",
                source_title="Tech News",
                source_url="https://tech.example.org",
            )
        active_provider.assert_called_once_with()
        self.assertEqual(spec.model_provider, "gapgpt")
        self.assertEqual(spec.model_name, "fixture-model")
        self.assertEqual(spec.prompt_version, "v1")
        self.assertEqual(spec.temperature, 0.1)

        # 2. Custom override
        custom_spec = resolver.resolve_spec(
            custom_config={
                "model_provider": "custom_mock_ai",
                "model_name": "mock-qwen-72b",
                "prompt_version": "v2_custom",
                "temperature": 0.7,
                "max_output_tokens": 2048,
                "language_policy": "fa_formal",
            }
        )
        self.assertEqual(custom_spec.model_provider, "custom_mock_ai")
        self.assertEqual(custom_spec.model_name, "mock-qwen-72b")
        self.assertEqual(custom_spec.prompt_version, "v2_custom")
        self.assertEqual(custom_spec.temperature, 0.7)
        self.assertEqual(custom_spec.max_output_tokens, 2048)
        self.assertEqual(custom_spec.language_policy, "fa_formal")

    # 12. Secret Exclusion in SourceAnalysisSpec
    def test_source_analysis_spec_secret_exclusion(self) -> None:
        """SourceAnalysisSpec strictly forbids credentials, secrets, and API tokens."""
        # Clean spec succeeds
        clean_spec = SourceAnalysisSpec(topic="Clean", source_title="Public")
        self.assertEqual(clean_spec.topic, "Clean")

        # Secret in value raises ValueError
        with self.assertRaises(ValueError):
            SourceAnalysisSpec(topic="bearer secret_token_xyz")

        with self.assertRaises(ValueError):
            SourceAnalysisSpec(topic="sk-live-1234567890abcdef")

        # Inspect class attributes: zero secret-like fields
        forbidden_terms = {"api_key", "secret", "password", "token", "credential"}
        for f in clean_spec.to_dict():
            self.assertNotIn(f.lower(), forbidden_terms)

    # 13. Test-Only Hook Audit
    def test_test_only_hook_audit(self) -> None:
        """Production classes must contain ZERO TEST_DELAY, artificial sleeps, or debug bypasses."""
        from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
        from app.modules.monitoring.infrastructure.source_execution_engine import SourceExecutionEngine

        for cls in (SingleSourceAnalyzer, SourceExecutionEngine, ExecuteSourceAnalysisUseCase):
            source_code = inspect.getsource(cls)
            self.assertNotIn("TEST_DELAY", source_code, f"{cls.__name__} must not have TEST_DELAY")
            self.assertNotIn("test_delay", source_code, f"{cls.__name__} must not have test_delay")
            self.assertNotIn("artificial", source_code, f"{cls.__name__} must not have artificial sleeps")

    # 14. No Fan-In Preservation
    async def test_no_fan_in_preservation(self) -> None:
        """Terminal source analysis does NOT complete or fail MonitoringRun; run stays RUNNING."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session)
            engine_inst = FakeControlledEngine(["Doc Final"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            dispatcher = MockAnalysisDispatcher()
            handoff = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine_inst,
                result_persister=persister,
                analysis_handoff=handoff,
            )
            await use_case.execute(task.id)

            analysis_id = await self._assert_durable_analysis_handoff(session, task.id)
            self.assertEqual(dispatcher.dispatched_ids, [])
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            result_repo = SqlAlchemySourceAnalysisResultRepository(session)
            doc_repo = SqlAlchemySourceTaskDocumentRepository(session)

            flaky = FlakyAnalyzer(transient_attempts=0)
            analysis_uc = ExecuteSourceAnalysisUseCase(
                analysis_repository=analysis_repo,
                result_repository=result_repo,
                document_reader=doc_repo,
                analyze_use_case=AnalyzeSourceUseCase(flaky),
            )
            res = await analysis_uc.execute(analysis_id)
            self.assertEqual(res["status"], "completed")

            # Invariant: MonitoringRun MUST still be RUNNING (No Fan-in in Phase 6A)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            final_run = await run_repo.get_by_id(run.id)
            self.assertEqual(final_run.status, MonitoringRunStatus.RUNNING)
