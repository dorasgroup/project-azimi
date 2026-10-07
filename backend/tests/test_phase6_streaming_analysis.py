"""Phase 6: Streaming Per-Source Analysis Comprehensive Test Suite."""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, date, datetime, timedelta
import inspect
import os
from pathlib import Path
import time
from typing import Sequence
from unittest import IsolatedAsyncioTestCase
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
    SOURCE_ANALYSIS_LEASE_SECONDS,
    AnalysisClaimStatus,
    SourceAnalysisResultData,
    SourceAnalysisSpec,
    SourceAnalysisStatus,
    compute_analysis_input_digest,
)
from app.modules.analysis.infrastructure.single_source_analyzer import (
    SingleSourceAnalyzer,
)
from app.modules.analysis.infrastructure.handoff_adapter import AnalysisHandoffAdapter
from app.modules.analysis.infrastructure.source_analysis_repository import (
    SqlAlchemySourceAnalysisRepository,
)
from app.modules.analysis.infrastructure.source_analysis_result_repository import (
    SqlAlchemySourceAnalysisResultRepository,
    SqlAlchemySourceTaskDocumentRepository,
)
from app.modules.analysis.ports.source_analysis import (
    SourceAnalysisDispatcherPort,
    SourceTaskDocumentRecord,
)
from app.modules.documents.infrastructure import SqlAlchemyDocumentRepository
from app.modules.documents.infrastructure.record_mapper import to_prepared_document_record
from app.modules.monitoring.application.execute_source_task import (
    ExecuteSourceTaskUseCase,
)
from app.modules.monitoring.application.source_failure import SourceFailureRecorder
from app.modules.monitoring.domain.execution import (
    ClaimOutcome,
    ClaimStatus,
    ExecutionMode,
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


class MockAnalysisDispatcher(SourceAnalysisDispatcherPort):
    def __init__(self) -> None:
        self.dispatched_ids: list[UUID] = []

    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str:
        self.dispatched_ids.append(source_analysis_id)
        return f"mock-task-{source_analysis_id}"


class ControlledAnalyzer:
    def __init__(self, delay_seconds: float = 0.0, fail: bool = False) -> None:
        self.delay_seconds = delay_seconds
        self.fail = fail
        self.calls: list[dict[str, object]] = []

    async def analyze_source(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData:
        self.calls.append({"spec": spec, "doc_count": len(documents), "topic": topic})
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("Controlled analysis failure")

        input_digest = compute_analysis_input_digest(
            source_id=documents[0].source_id if documents else uuid4(),
            topic=topic,
            spec=spec,
            documents=documents,
        )
        return SourceAnalysisResultData(
            schema_version="1.0",
            input_digest=input_digest,
            summary=f"Analysis of {len(documents)} docs on {topic}",
            findings=[
                {"title": "Finding 1", "explanation": "Details 1", "document_url": documents[0].url if documents else ""}
            ],
            evidence_map={doc.url: f"Evidence for {doc.url}" for doc in documents},
            raw_result={"mock": True},
        )


class ControlledCrawlEngine(SourceExecutionEnginePort):
    def __init__(self, doc_titles: list[str], sleep_seconds: float = 0.0) -> None:
        self.doc_titles = doc_titles
        self.sleep_seconds = sleep_seconds

    async def execute_crawl(self, spec: SourceExecutionSpec):
        if self.sleep_seconds > 0:
            await asyncio.sleep(self.sleep_seconds)

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

        from app.processing.schemas import PreparedDocument

        docs = []
        for i, t in enumerate(self.doc_titles):
            doc_url = f"https://example.org/p/{uuid4().hex[:8]}_{i}"
            docs.append(PreparedDocument(
                url=doc_url,
                canonical_url=doc_url,
                title=t,
                clean_text=f"Clean text for {t} with details.",
                markdown=f"# {t}\nClean text",
                author="Author",
                source_name="Source",
                published_at=datetime.now(UTC),
                published_at_jalali="1403/01/01",
                language="fa",
                lead_image_url=None,
                matched_score=0.95,
                matched_terms=["test"],
                content_hash=f"hash_{t}_{i}",
                published_at_source="2026-09-26",
                published_at_confidence=1.0,
                published_at_raw="2026-09-26",
                metadata={},
            ))

        class DummyPreparedBatch:
            pass

        batch = DummyPreparedBatch()
        batch.documents = docs
        batch.rejected = []
        batch.duplicate_count = 0

        class DummyCrawlPolicy:
            max_pages = len(self.doc_titles)

        return DummyCrawlResult(), batch, DummyCrawlPolicy()


class TestPhase6StreamingAnalysis(IsolatedAsyncioTestCase):
    """Full contract and integration test suite for Phase 6 Streaming Per-Source Analysis."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()

    async def asyncTearDown(self) -> None:
        await engine.dispose()

    async def _setup_environment(
        self, session: AsyncSession, execution_mode: str = "distributed"
    ) -> tuple[MonitoringJob, OrmMonitoringRun, Source, OrmSourceTask]:
        user = await session.scalar(select(User).limit(1))
        user_id = user.id if user else uuid4()
        bot = await session.scalar(select(BotIdentity).limit(1))
        bot_id = bot.id if bot else uuid4()

        source = Source(
            id=uuid4(),
            title=f"Source P6 {uuid4().hex[:4]}",
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
            topic="ai regulation",
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
        await session.flush()

        run = OrmMonitoringRun(
            id=uuid4(),
            monitoring_job_id=job.id,
            execution_mode=execution_mode,
            status=MonitoringRunStatus.RUNNING,
            expected_sources=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(run)
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
                "topic": "ai regulation",
                "date_from": "2026-01-01",
                "date_to": "2026-01-10",
                "crawl_config": {},
                "base_url": source.base_url,
                "title": source.title,
                "spec_version": 1,
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(task)
        await session.commit()
        return job, run, source, task

    async def test_document_snapshot_isolation_same_source_different_runs(self) -> None:
        """Run 1 and Run 2 crawling the same source produce isolated snapshots in source_task_documents."""
        async with SessionFactory() as session:
            job, run1, source, task1 = await self._setup_environment(session, "distributed")
            user = await session.scalar(select(User).limit(1))
            bot = await session.scalar(select(BotIdentity).limit(1))
            bot_id = bot.id if bot else uuid4()

            # Create a second job for run2 (unique partial index prevents two active runs on same job)
            job2 = MonitoringJob(
                id=uuid4(),
                topic="ai regulation run 2",
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
            session.add(job2)
            await session.flush()

            run2 = OrmMonitoringRun(
                id=uuid4(),
                monitoring_job_id=job2.id,
                execution_mode="distributed",
                status=MonitoringRunStatus.RUNNING,
                expected_sources=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(run2)
            await session.flush()

            task2 = OrmSourceTask(
                id=uuid4(),
                run_id=run2.id,
                source_id=source.id,
                ordinal=1,
                status=SourceTaskStatus.PENDING,
                attempt_count=0,
                execution_spec=task1.execution_spec,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(task2)
            await session.commit()

            # Execute Task 1 with Doc A
            engine1 = ControlledCrawlEngine(doc_titles=["Article from Run 1"])
            persister1 = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            use_case1 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine1,
                result_persister=persister1,
            )
            res1 = await use_case1.execute(task1.id)
            self.assertEqual(res1["status"], "completed")

            # Execute Task 2 with Doc B
            engine2 = ControlledCrawlEngine(doc_titles=["Article from Run 2"])
            persister2 = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
            use_case2 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session),
                run_repository=SqlAlchemyMonitoringRunRepository(session),
                execution_engine=engine2,
                result_persister=persister2,
            )
            res2 = await use_case2.execute(task2.id)
            self.assertEqual(res2["status"], "completed")

            # Verify isolated snapshots
            doc_repo = SqlAlchemySourceTaskDocumentRepository(session)
            docs_task1 = await doc_repo.get_documents_for_task(task1.id)
            docs_task2 = await doc_repo.get_documents_for_task(task2.id)

            self.assertEqual(len(docs_task1), 1)
            self.assertEqual(len(docs_task2), 1)
            self.assertEqual(docs_task1[0].title, "Article from Run 1")
            self.assertEqual(docs_task2[0].title, "Article from Run 2")
            self.assertEqual(docs_task1[0].run_id, run1.id)
            self.assertEqual(docs_task2[0].run_id, run2.id)
            self.assertNotEqual(docs_task1[0].url, docs_task2[0].url)

    # 2. Input Digest Provenance & Determinism
    async def test_analysis_input_digest_provenance_determinism(self) -> None:
        """Deterministic computation of input digest based on source_id, topic, spec, and documents."""
        source_id = uuid4()
        topic = "climate technology"
        spec = SourceAnalysisSpec(
            model_provider="openai",
            model_name="gpt-4o-mini",
            temperature=0.1,
            topic=topic,
        )

        class DocStub:
            def __init__(self, url: str, text: str, h: str) -> None:
                self.url = url
                self.clean_text = text
                self.content_hash = h

        docs_a = [
            DocStub("https://example.com/b", "content b", "hash_b"),
            DocStub("https://example.com/a", "content a", "hash_a"),
        ]
        # Same docs in different order
        docs_b = [
            DocStub("https://example.com/a", "content a", "hash_a"),
            DocStub("https://example.com/b", "content b", "hash_b"),
        ]

        digest_1 = compute_analysis_input_digest(source_id, topic, spec, docs_a)
        digest_2 = compute_analysis_input_digest(source_id, topic, spec, docs_b)

        # Order independent because helper sorts by URL
        self.assertEqual(digest_1, digest_2)

        # Mutate 1 document
        docs_mutated = [
            DocStub("https://example.com/a", "content a", "hash_a_different"),
            DocStub("https://example.com/b", "content b", "hash_b"),
        ]
        digest_mutated = compute_analysis_input_digest(source_id, topic, spec, docs_mutated)
        self.assertNotEqual(digest_1, digest_mutated)

    # 3. Spec Mutation Isolation
    async def test_analysis_spec_snapshot_mutation_isolation(self) -> None:
        """Captured SourceAnalysisSpec is immutable and immune to later setting changes."""
        spec_initial = SourceAnalysisSpec(
            model_name="gpt-4o-mini",
            temperature=0.1,
            topic="initial topic",
        )
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session, "distributed")
            repo = SqlAlchemySourceAnalysisRepository(session)
            analysis = await repo.ensure_for_source_task(
                source_task_id=task.id,
                run_id=run.id,
                source_id=source.id,
                spec=spec_initial,
            )
            # Re-fetch from DB
            fetched = await repo.get_by_id(analysis.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.analysis_spec.model_name, "gpt-4o-mini")
            self.assertEqual(fetched.analysis_spec.temperature, 0.1)

            # Global settings or variable change later does not touch the captured row
            settings_changed_spec = SourceAnalysisSpec(
                model_name="gpt-4o",
                temperature=0.7,
                topic="modified topic",
            )
            # Ensure idempotent call does not overwrite the existing spec
            idempotent_analysis = await repo.ensure_for_source_task(
                source_task_id=task.id,
                run_id=run.id,
                source_id=source.id,
                spec=settings_changed_spec,
            )
            self.assertEqual(idempotent_analysis.analysis_spec.model_name, "gpt-4o-mini")

    # 4. Zero Document Behavior
    async def test_zero_document_behavior(self) -> None:
        """When 0 documents exist, SingleSourceAnalyzer terminates with COMPLETED and structured result without provider call."""
        from unittest.mock import AsyncMock
        mock_gateway = AsyncMock()

        analyzer = SingleSourceAnalyzer(mock_gateway)
        spec = SourceAnalysisSpec(topic="zero docs test")
        result = await analyzer.analyze_source(spec, [], "zero docs test")

        mock_gateway.generate_json.assert_not_called()
        self.assertEqual(result.raw_result.get("status"), "no_documents")
        self.assertIn("فاقد سند", result.summary)
        self.assertEqual(result.findings, [])

    # 5. Duplicate Delivery Fencing
    async def test_duplicate_analysis_delivery_fencing(self) -> None:
        """Two concurrent deliveries of the same analysis ID: one claims, second is fenced."""
        async with SessionFactory() as session1, SessionFactory() as session2:
            job, run, source, task = await self._setup_environment(session1, "distributed")
            spec = SourceAnalysisSpec(topic="concurrency fencing")
            analysis_repo1 = SqlAlchemySourceAnalysisRepository(session1)
            analysis = await analysis_repo1.ensure_for_source_task(task.id, run.id, source.id, spec)

            # Concurrent claims
            analysis_repo2 = SqlAlchemySourceAnalysisRepository(session2)

            claim1, claim2 = await asyncio.gather(
                analysis_repo1.claim_explicit(analysis.id),
                analysis_repo2.claim_explicit(analysis.id),
            )

            statuses = {claim1.status, claim2.status}
            self.assertIn(AnalysisClaimStatus.CLAIMED, statuses)
            self.assertTrue(
                AnalysisClaimStatus.ACTIVE_LEASE in statuses or AnalysisClaimStatus.ALREADY_TERMINAL in statuses
            )

    # 6. Stale Analysis Result Rejected by Side-Effect Fence
    async def test_stale_analysis_result_rejected_by_fence(self) -> None:
        """Worker A claims analysis; lease expires; Worker B reclaims and completes; Worker A persistence rejected."""
        async with SessionFactory() as sessionA, SessionFactory() as sessionB:
            job, run, source, task = await self._setup_environment(sessionA, "distributed")
            spec = SourceAnalysisSpec(topic="stale fence test")
            repoA = SqlAlchemySourceAnalysisRepository(sessionA)
            analysis = await repoA.ensure_for_source_task(task.id, run.id, source.id, spec)

            # Worker A claims with lease = 1 second
            claimA = await repoA.claim_explicit(analysis.id, lease_seconds=1)
            self.assertEqual(claimA.status, AnalysisClaimStatus.CLAIMED)
            tokenA = claimA.claim_token

            # Simulate lease expiration
            await asyncio.sleep(1.1)

            # Worker B re-claims after expiration
            repoB = SqlAlchemySourceAnalysisRepository(sessionB)
            claimB = await repoB.claim_explicit(analysis.id, lease_seconds=60)
            self.assertEqual(claimB.status, AnalysisClaimStatus.CLAIMED)
            tokenB = claimB.claim_token
            self.assertNotEqual(tokenA, tokenB)

            # Worker B saves result and completes
            result_repoB = SqlAlchemySourceAnalysisResultRepository(sessionB)
            dummy_result = SourceAnalysisResultData(
                schema_version="1.0",
                input_digest="digest_b",
                summary="Summary B",
                findings=[],
                evidence_map={},
                raw_result={},
            )
            savedB = await result_repoB.save_result_fenced(analysis.id, tokenB, dummy_result)
            self.assertTrue(savedB)
            completedB = await repoB.complete_analysis(analysis.id, tokenB, input_digest="digest_b")
            self.assertTrue(completedB)

            # Worker A wakes up and attempts save_result_fenced with tokenA -> REJECTED
            result_repoA = SqlAlchemySourceAnalysisResultRepository(sessionA)
            savedA = await result_repoA.save_result_fenced(analysis.id, tokenA, dummy_result)
            self.assertFalse(savedA)

    # 7. Source -> Analysis Handoff
    async def test_source_to_analysis_handoff_and_dispatch(self) -> None:
        """Source commit records a durable handoff; consumer atomically ensures analysis."""
        dispatcher = MockAnalysisDispatcher()
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session, "distributed")
            engine_inst = ControlledCrawlEngine(doc_titles=["Article 1", "Article 2"])
            persister = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )
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
                analysis_handoff=None,
            )
            res = await use_case.execute(task.id)
            self.assertEqual(res["status"], "completed")

            parent = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis-handoff:{task.id}"))
            self.assertIsNotNone(parent)
            self.assertIsNone(await session.scalar(select(OrmSourceAnalysis).where(
                OrmSourceAnalysis.source_task_id == task.id)))
            self.assertEqual(dispatcher.dispatched_ids, [])

            await handoff.ensure_and_dispatch(task.id, consumed_outbox_id=parent.id)
            await session.refresh(parent)
            self.assertIsNotNone(parent.consumer_acked_at)
            successor = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis:{task.id}"))
            self.assertIsNotNone(successor)
            self.assertEqual(successor.message_type, "SOURCE_ANALYSIS")
            self.assertEqual(dispatcher.dispatched_ids, [])
            dispatched_analysis_id = UUID(successor.payload_json["source_analysis_id"])

            # Verify SourceAnalysis exists in DB with PENDING
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            analysis = await analysis_repo.get_by_id(dispatched_analysis_id)
            self.assertIsNotNone(analysis)
            self.assertEqual(analysis.status, SourceAnalysisStatus.PENDING)
            self.assertEqual(analysis.source_task_id, task.id)

    # 8. Crash Before Publish Redelivery Recovery
    async def test_crash_before_publish_redelivery_recovery(self) -> None:
        """A source crash after commit leaves a relay-recoverable handoff, without recrawl."""
        dispatcher = MockAnalysisDispatcher()
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session, "distributed")
            # Step 1: Simulate source task committed without dispatch (persister has no dispatcher)
            engine_inst = ControlledCrawlEngine(doc_titles=["Article Crash Recovery"])
            persister_no_dispatch = SourceResultPersister(
                session_factory=SessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
            )

        async with SessionFactory() as session1:
            use_case1 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session1),
                run_repository=SqlAlchemyMonitoringRunRepository(session1),
                execution_engine=engine_inst,
                result_persister=persister_no_dispatch,
            )
            await use_case1.execute(task.id)
        self.assertEqual(len(dispatcher.dispatched_ids), 0)
        async with SessionFactory() as check_session:
            parent = await check_session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis-handoff:{task.id}"))
            self.assertIsNotNone(parent)
            parent_id = parent.id

        # Step 2: the durable handoff consumer resumes; no source recrawl.
        async with SessionFactory() as session2:
            handoff2 = AnalysisHandoffAdapter(
                session_factory=SessionFactory,
                analysis_dispatcher=dispatcher,
                analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
            )
            await handoff2.ensure_and_dispatch(task.id, consumed_outbox_id=parent_id)
            successor = await session2.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == f"source-analysis:{task.id}"))
            self.assertIsNotNone(successor)
            parent = await session2.get(IntegrationOutbox, parent_id)
            self.assertIsNotNone(parent.consumer_acked_at)
            use_case2 = ExecuteSourceTaskUseCase(
                task_repository=SqlAlchemySourceTaskRepository(session2),
                run_repository=SqlAlchemyMonitoringRunRepository(session2),
                execution_engine=engine_inst,
                result_persister=persister_no_dispatch,
                analysis_handoff=handoff2,
            )
            outcome = await use_case2.execute(task.id)
            self.assertEqual(outcome["status"], "already_terminal")

        # Duplicate source delivery is harmless; no direct Celery publisher used.
        self.assertEqual(dispatcher.dispatched_ids, [])
        async with SessionFactory() as check_session:
            analyses = (await check_session.execute(select(OrmSourceAnalysis).where(
                OrmSourceAnalysis.source_task_id == task.id))).scalars().all()
            self.assertEqual(len(analyses), 1)

    # 9. Streaming Proof (Critical): Analysis A Starts Before Crawl C Finishes
    async def test_streaming_proof_analysis_a_starts_before_crawl_c_finishes(self) -> None:
        """Strict proof: analysis_A_started < crawl_C_finished."""
        timestamps: dict[str, float] = {}
        async with SessionFactory() as session:
            job, run, sourceA, taskA = await self._setup_environment(session, "distributed")

            # Setup Source B and C
            user = await session.scalar(select(User).limit(1))
            sourceB = Source(
                id=uuid4(),
                title="Source B Medium",
                base_url=f"https://source-b-{uuid4().hex[:6]}.example.org",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.SUCCESS,
                created_by=user.id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            sourceC = Source(
                id=uuid4(),
                title="Source C Slow",
                base_url=f"https://source-c-{uuid4().hex[:6]}.example.org",
                type=SourceType.NEWS_SITE,
                last_status=SourceStatus.SUCCESS,
                created_by=user.id,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add_all([sourceB, sourceC])
            await session.flush()

            taskB = OrmSourceTask(
                id=uuid4(),
                run_id=run.id,
                source_id=sourceB.id,
                ordinal=2,
                status=SourceTaskStatus.PENDING,
                attempt_count=0,
                execution_spec={
                    "source_id": str(sourceB.id),
                    "topic": "ai regulation",
                    "date_from": "2026-01-01",
                    "date_to": "2026-01-10",
                    "crawl_config": {},
                    "base_url": sourceB.base_url,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            taskC = OrmSourceTask(
                id=uuid4(),
                run_id=run.id,
                source_id=sourceC.id,
                ordinal=3,
                status=SourceTaskStatus.PENDING,
                attempt_count=0,
                execution_spec={
                    "source_id": str(sourceC.id),
                    "topic": "ai regulation",
                    "date_from": "2026-01-01",
                    "date_to": "2026-01-10",
                    "crawl_config": {},
                    "base_url": sourceC.base_url,
                },
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add_all([taskB, taskC])
            await session.commit()

        # Engine delays:
        # A = fast (0.05s)
        # B = medium (0.25s)
        # C = slow (0.50s)
        engineA = ControlledCrawlEngine(["Doc A"], sleep_seconds=0.05)
        engineB = ControlledCrawlEngine(["Doc B"], sleep_seconds=0.25)
        engineC = ControlledCrawlEngine(["Doc C"], sleep_seconds=0.50)

        async def _run_source(t_id, eng, label):
            async with SessionFactory() as sess:
                persister = SourceResultPersister(
                    session_factory=SessionFactory,
                    monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                    document_repositories=SqlAlchemyDocumentRepository,
                    map_document=to_prepared_document_record,
                    failure_recorder=SourceFailureRecorder(SqlAlchemyPipelinePersistenceAdapter(SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository)),
                )
                handoff = AnalysisHandoffAdapter(
                    session_factory=SessionFactory,
                    analysis_dispatcher=MockAnalysisDispatcher(),
                    analysis_repo_factory=SqlAlchemySourceAnalysisRepository,
                )
                uc = ExecuteSourceTaskUseCase(
                    task_repository=SqlAlchemySourceTaskRepository(sess),
                    run_repository=SqlAlchemyMonitoringRunRepository(sess),
                    execution_engine=eng,
                    result_persister=persister,
                    analysis_handoff=handoff,
                )
                await uc.execute(t_id)
                timestamps[f"crawl_{label}_finished"] = time.time()

        async def _run_analysis_A_when_ready():
            # Wait for task A to complete crawl
            while "crawl_A_finished" not in timestamps:
                await asyncio.sleep(0.01)

            timestamps["analysis_A_started"] = time.time()
            async with SessionFactory() as sess:
                analysis_repo = SqlAlchemySourceAnalysisRepository(sess)
                analysis = await analysis_repo.get_by_source_task_id(taskA.id)
                self.assertIsNotNone(analysis)

                analyzer = ControlledAnalyzer(delay_seconds=0.05)
                uc = ExecuteSourceAnalysisUseCase(
                    analysis_repository=analysis_repo,
                    result_repository=SqlAlchemySourceAnalysisResultRepository(sess),
                    document_reader=SqlAlchemySourceTaskDocumentRepository(sess),
                    analyze_use_case=AnalyzeSourceUseCase(analyzer),
                )
                await uc.execute(analysis.id)
                timestamps["analysis_A_finished"] = time.time()

        # Run concurrent pipeline: Crawl A, B, C and streaming Analysis A
        await asyncio.gather(
            _run_source(taskA.id, engineA, "A"),
            _run_source(taskB.id, engineB, "B"),
            _run_source(taskC.id, engineC, "C"),
            _run_analysis_A_when_ready(),
        )

        crawl_A_finished = timestamps["crawl_A_finished"]
        analysis_A_started = timestamps["analysis_A_started"]
        crawl_B_finished = timestamps["crawl_B_finished"]
        crawl_C_finished = timestamps["crawl_C_finished"]

        print(f"\n[STREAMING TIMELINE] crawl_A_finished={crawl_A_finished:.3f}, analysis_A_started={analysis_A_started:.3f}, crawl_B_finished={crawl_B_finished:.3f}, crawl_C_finished={crawl_C_finished:.3f}")

        # Core invariant: Analysis of A started BEFORE crawl of C finished
        self.assertLess(analysis_A_started, crawl_C_finished)
        self.assertLess(analysis_A_started, crawl_B_finished)

    # 10. Independent Analysis Concurrency Overlap
    async def test_independent_analysis_concurrency_overlap(self) -> None:
        """Multiple analyses execute concurrently with temporal overlap."""
        analysis_windows: dict[str, tuple[float, float]] = {}

        async def _simulate_worker(worker_id: str):
            async with SessionFactory() as sess:
                job, run, source, task = await self._setup_environment(sess, "distributed")
                spec = SourceAnalysisSpec(topic=f"topic {worker_id}")
                repo = SqlAlchemySourceAnalysisRepository(sess)
                analysis = await repo.ensure_for_source_task(task.id, run.id, source.id, spec)

                analyzer = ControlledAnalyzer(delay_seconds=0.15)
                uc = ExecuteSourceAnalysisUseCase(
                    analysis_repository=repo,
                    result_repository=SqlAlchemySourceAnalysisResultRepository(sess),
                    document_reader=SqlAlchemySourceTaskDocumentRepository(sess),
                    analyze_use_case=AnalyzeSourceUseCase(analyzer),
                )
                start_t = time.time()
                await uc.execute(analysis.id)
                end_t = time.time()
                analysis_windows[worker_id] = (start_t, end_t)

        await asyncio.gather(
            _simulate_worker("W1"),
            _simulate_worker("W2"),
            _simulate_worker("W3"),
        )

        # Check overlap between W1 and W2
        (s1, e1) = analysis_windows["W1"]
        (s2, e2) = analysis_windows["W2"]
        overlap = max(s1, s2) < min(e1, e2)
        self.assertTrue(overlap, "Worker 1 and Worker 2 must have temporal overlap")

    # 11. Clean Architecture Guardrails
    def test_clean_architecture_guardrails(self) -> None:
        """Domain and Application layers must have ZERO Celery or Redis imports."""
        backend_dir = Path("/opt/rasad10/backend/app")
        forbidden = {"celery", "redis"}

        dirs_to_check = [
            backend_dir / "modules" / "analysis" / "domain",
            backend_dir / "modules" / "analysis" / "application",
            backend_dir / "modules" / "analysis" / "ports",
        ]

        for check_dir in dirs_to_check:
            for py_file in check_dir.rglob("*.py"):
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            root_pkg = alias.name.split(".")[0]
                            self.assertNotIn(
                                root_pkg,
                                forbidden,
                                f"Forbidden import '{alias.name}' in {py_file}",
                            )
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            root_pkg = node.module.split(".")[0]
                            self.assertNotIn(
                                root_pkg,
                                forbidden,
                                f"Forbidden from-import '{node.module}' in {py_file}",
                            )

    # 12. No Fan-In & MonitoringRun Remains Awaiting Phase 7
    async def test_no_fan_in_and_monitoring_run_remains_running(self) -> None:
        """Completion of all SourceAnalysis tasks must NOT complete MonitoringRun (Phase 7 boundary)."""
        async with SessionFactory() as session:
            job, run, source, task = await self._setup_environment(session, "distributed")
            spec = SourceAnalysisSpec(topic="no fan-in test")
            repo = SqlAlchemySourceAnalysisRepository(session)
            analysis = await repo.ensure_for_source_task(task.id, run.id, source.id, spec)

            analyzer = ControlledAnalyzer(delay_seconds=0.01)
            uc = ExecuteSourceAnalysisUseCase(
                analysis_repository=repo,
                result_repository=SqlAlchemySourceAnalysisResultRepository(session),
                document_reader=SqlAlchemySourceTaskDocumentRepository(session),
                analyze_use_case=AnalyzeSourceUseCase(analyzer),
            )
            res = await uc.execute(analysis.id)
            self.assertEqual(res["status"], "completed")

            # Check MonitoringRun status in DB
            run_db = await session.get(OrmMonitoringRun, run.id)
            self.assertIsNotNone(run_db)
            self.assertEqual(run_db.status, MonitoringRunStatus.RUNNING)
            self.assertNotEqual(run_db.status, MonitoringRunStatus.COMPLETED)
