"""Phase 6B: Analysis Crash Redelivery & Run-Level Configuration Consistency Test Suite."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import os
from typing import Sequence
from unittest import IsolatedAsyncioTestCase
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import SessionFactory, engine
from app.models.bot_identity import BotIdentity
from app.models.monitoring_job import JobStatus, MonitoringJob
from app.models.integration_outbox import IntegrationOutbox
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.source import Source, SourceStatus, SourceType
from app.models.source_analysis import (
    OrmRunAnalysisSpec,
    OrmSourceAnalysis,
    OrmSourceAnalysisResult,
)
from app.models.source_task import SourceTask as OrmSourceTask
from app.models.source_task_document import SourceTaskDocument
from app.models.user import User
from app.modules.analysis.application.source_analysis_use_cases import (
    AnalyzeSourceUseCase,
    ExecuteSourceAnalysisUseCase,
)
from app.modules.analysis.domain.run_analysis_spec import (
    RunAnalysisSpec,
    compute_run_spec_digest,
    validate_run_spec_secrets,
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
from app.modules.analysis.infrastructure.run_analysis_spec_repository import (
    SqlAlchemyRunAnalysisSpecRepository,
)
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
from app.processing.schemas import PreparedDocument
from app.workers.analysis_tasks import process_source_analysis
from app.workers.source_tasks import process_source_task


class MockAnalysisDispatcher(SourceAnalysisDispatcherPort):
    def __init__(self) -> None:
        self.dispatched_ids: list[UUID] = []

    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str:
        self.dispatched_ids.append(source_analysis_id)
        return f"mock-analysis-{source_analysis_id}"


class MockAnalyzer:
    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.delay_seconds = delay_seconds
        self.invocations: list[UUID] = []

    async def analyze_source(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)
        doc_count = len(documents)
        return SourceAnalysisResultData(
            schema_version="1.0",
            input_digest="test-digest-phase6b",
            summary=f"Analysis for {spec.source_title}: {doc_count} documents analyzed.",
            findings=[{"text": f"Finding for {spec.source_title}", "confidence": 0.95}],
            evidence_map={"sample_key": "sample_evidence"},
            raw_result={"model": spec.model_name, "provider": spec.model_provider},
        )


class FlakyAnalyzer:
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
            raise TransientAnalysisError(f"HTTP 429 rate limit exceeded (attempt {self.attempt_call_count})")
        return SourceAnalysisResultData(
            schema_version="1.0",
            input_digest="recovered-digest",
            summary="Recovered after transient rate limit",
            findings=[{"text": "Successful recovery"}],
            evidence_map={},
            raw_result={},
        )


@pytest.mark.asyncio
class TestPhase6BRedeliveryAndRunConfig(IsolatedAsyncioTestCase):
    """Phase 6B test cases verifying ACTIVE_LEASE crash recovery and run-level config consistency."""

    async def asyncSetUp(self) -> None:
        await engine.dispose()
        self.session = SessionFactory()
        await self._setup_environment(self.session)

        self.run_repo = SqlAlchemyMonitoringRunRepository(self.session)
        self.task_repo = SqlAlchemySourceTaskRepository(self.session)
        self.analysis_repo = SqlAlchemySourceAnalysisRepository(self.session)
        self.result_repo = SqlAlchemySourceAnalysisResultRepository(self.session)
        self.doc_repo = SqlAlchemySourceTaskDocumentRepository(self.session)
        self.run_spec_repo = SqlAlchemyRunAnalysisSpecRepository(self.session)
        self.dispatcher = MockAnalysisDispatcher()
        self.handoff_adapter = AnalysisHandoffAdapter(
            session_factory=SessionFactory,
            analysis_dispatcher=self.dispatcher,
            analysis_repo_factory=lambda s: SqlAlchemySourceAnalysisRepository(s),
            run_spec_repo_factory=lambda s: SqlAlchemyRunAnalysisSpecRepository(s),
        )

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await engine.dispose()

    async def _setup_environment(self, session: AsyncSession) -> None:
        user = await session.scalar(select(User).limit(1))
        if not user:
            user = User(
                id=uuid4(),
                email=f"p6b-{uuid4().hex[:6]}@example.org",
                password_hash="fake",
                full_name="Phase 6B Tester",
                is_active=True,
            )
            session.add(user)
            await session.flush()

        bot = await session.scalar(select(BotIdentity).limit(1))
        bot_id = bot.id if bot else uuid4()

        self.job = MonitoringJob(
            id=uuid4(),
            topic="phase 6b topic",
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
        session.add(self.job)
        await session.flush()

        self.run = OrmMonitoringRun(
            id=uuid4(),
            monitoring_job_id=self.job.id,
            execution_mode="distributed",
            status=MonitoringRunStatus.RUNNING,
            expected_sources=2,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(self.run)
        await session.flush()

        self.source1 = Source(
            id=uuid4(),
            title=f"Source 6B Alpha {uuid4().hex[:4]}",
            base_url=f"https://alpha-{uuid4().hex}.example.org",
            type=SourceType.NEWS_SITE,
            last_status=SourceStatus.SUCCESS,
            created_by=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.source2 = Source(
            id=uuid4(),
            title=f"Source 6B Beta {uuid4().hex[:4]}",
            base_url=f"https://beta-{uuid4().hex}.example.org",
            type=SourceType.NEWS_SITE,
            last_status=SourceStatus.SUCCESS,
            created_by=user.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add_all([self.source1, self.source2])
        await session.flush()

        self.task1 = OrmSourceTask(
            id=uuid4(),
            run_id=self.run.id,
            source_id=self.source1.id,
            ordinal=1,
            status=SourceTaskStatus.PENDING,
            attempt_count=0,
            execution_spec={
                "source_id": str(self.source1.id),
                "topic": self.job.topic,
                "date_from": "2026-01-01",
                "date_to": "2026-01-10",
                "crawl_config": {},
                "base_url": self.source1.base_url,
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.task2 = OrmSourceTask(
            id=uuid4(),
            run_id=self.run.id,
            source_id=self.source2.id,
            ordinal=2,
            status=SourceTaskStatus.PENDING,
            attempt_count=0,
            execution_spec={
                "source_id": str(self.source2.id),
                "topic": self.job.topic,
                "date_from": "2026-01-01",
                "date_to": "2026-01-10",
                "crawl_config": {},
                "base_url": self.source2.base_url,
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add_all([self.task1, self.task2])
        await session.commit()

    # -------------------------------------------------------------------------
    # 0. Characterization Test: ACTIVE_LEASE Crash Window Contract
    # -------------------------------------------------------------------------
    async def test_characterization_active_lease_crash_loss_window_contract(self) -> None:
        """Characterization test: documents the before-fix crash loss window vs after-fix retry.

        If a worker crashed while status=RUNNING and lease_expires_at > now, returning {'status': 'active_lease'}
        normally from Celery caused an ACK, permanently losing the task.
        In Phase 6B, process_source_analysis detects 'active_lease' and raises Celery Retry.
        """
        with patch.object(process_source_analysis, "retry", side_effect=Retry("Simulated delayed retry")) as mock_retry:
            with patch("app.workers.analysis_tasks.run_source_analysis_worker") as mock_run_worker:
                mock_run_worker.return_value = {
                    "status": "active_lease",
                    "analysis_id": str(uuid4()),
                    "lease_expires_at": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
                }
                with pytest.raises(Retry):
                    process_source_analysis(str(uuid4()))

                mock_retry.assert_called_once()
                _, kwargs = mock_retry.call_args
                assert kwargs["countdown"] >= 50
                assert kwargs.get("max_retries") is None

    # -------------------------------------------------------------------------
    # 1. ACTIVE_LEASE Redelivery is NOT Acked Away
    # -------------------------------------------------------------------------
    async def test_active_lease_redelivery_is_not_acked_away(self) -> None:
        """When an Analysis task is redelivered while DB lease is active, Celery MUST NOT ACK it away.

        It must raise Celery Retry with countdown past the lease expiration.
        """
        spec = SourceAnalysisSpec(topic="test", source_title="S1")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        # Simulate Worker A claiming the analysis with 60s lease
        worker_a_token = uuid4()
        claim_outcome = await self.analysis_repo.claim_explicit(
            analysis.id,
            worker_token=worker_a_token,
            lease_seconds=60,
        )
        assert claim_outcome.status == AnalysisClaimStatus.CLAIMED

        with patch.object(process_source_analysis, "retry", side_effect=Retry("Simulated delayed retry")) as mock_retry:
            with patch("app.workers.analysis_tasks.run_source_analysis_worker") as mock_run_worker:
                mock_run_worker.return_value = {
                    "status": "active_lease",
                    "analysis_id": str(analysis.id),
                    "lease_expires_at": claim_outcome.lease_expires_at.isoformat(),
                }
                with pytest.raises(Retry):
                    process_source_analysis(str(analysis.id))

                mock_retry.assert_called_once()
                _, kwargs = mock_retry.call_args
                assert "countdown" in kwargs
                assert kwargs["countdown"] >= 50
                assert kwargs.get("max_retries") is None

    # -------------------------------------------------------------------------
    # 2. ACTIVE_LEASE Retry Executes After Expiry
    # -------------------------------------------------------------------------
    async def test_active_lease_retry_executes_after_expiry(self) -> None:
        """After active lease expires, subsequent reclaim succeeds and analysis executes."""
        spec = SourceAnalysisSpec(topic="test", source_title="S1")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        # Worker A claims with short lease that has already expired in the past
        past_time = datetime.now(UTC) - timedelta(seconds=10)
        await self.session.execute(
            text(
                "UPDATE source_analyses SET status='running', claimed_at=:past, "
                "lease_expires_at=:past, attempt_count=1 WHERE id=:aid"
            ),
            {"past": past_time, "aid": analysis.id},
        )
        await self.session.commit()

        # Worker B arrives after expiry -> claim succeeds (attempt_count incremented to 2)
        analyzer = MockAnalyzer()
        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(analyzer),
        )
        outcome = await use_case.execute(analysis.id)
        assert outcome["status"] == "completed"

        # Verify DB status
        refreshed = await self.analysis_repo.get_by_id(analysis.id)
        assert refreshed.status == SourceAnalysisStatus.COMPLETED
        assert refreshed.attempt_count == 2

    # -------------------------------------------------------------------------
    # 3. Duplicate Delivery Finishes as Terminal No-op
    # -------------------------------------------------------------------------
    async def test_active_lease_duplicate_finishes_as_terminal_noop(self) -> None:
        """When Worker A completes analysis, a duplicate delivery to Worker B encounters

        ALREADY_TERMINAL and finishes cleanly with ACK without invoking analyzer.
        """
        spec = SourceAnalysisSpec(topic="test", source_title="S1")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        # Worker A completes analysis
        analyzer = MockAnalyzer()
        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(analyzer),
        )
        await use_case.execute(analysis.id)

        # Worker B receives duplicate message
        with patch.object(process_source_analysis, "retry") as mock_retry:
            with patch("app.workers.analysis_tasks.run_source_analysis_worker") as mock_run_worker:
                mock_run_worker.return_value = {
                    "status": "already_terminal",
                    "analysis_id": str(analysis.id),
                }
                res = process_source_analysis(str(analysis.id))
                assert res["status"] == "already_terminal"
                mock_retry.assert_not_called()

    # -------------------------------------------------------------------------
    # 4. Worker Crash Immediate Redelivery Recovers
    # -------------------------------------------------------------------------
    async def test_worker_crash_immediate_redelivery_recovers(self) -> None:
        """Full lifecycle: Worker A claims -> crashes -> immediate redelivery -> delayed retry

        -> lease expires -> Worker B claims and finishes exactly once.
        """
        spec = SourceAnalysisSpec(topic="test", source_title="S1")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        # Worker A claims with 30s lease
        worker_a_token = uuid4()
        claim1 = await self.analysis_repo.claim_explicit(analysis.id, worker_token=worker_a_token, lease_seconds=30)
        assert claim1.status == AnalysisClaimStatus.CLAIMED

        # Worker A crashes. Broker redelivers immediately to Worker B
        with patch.object(process_source_analysis, "retry", side_effect=Retry("Delayed retry scheduled")):
            with patch("app.workers.analysis_tasks.run_source_analysis_worker") as mock_run_worker:
                mock_run_worker.return_value = {
                    "status": "active_lease",
                    "analysis_id": str(analysis.id),
                    "lease_expires_at": claim1.lease_expires_at.isoformat(),
                }
                with pytest.raises(Retry):
                    process_source_analysis(str(analysis.id))

        # Advance DB clock past lease expiry
        past_time = datetime.now(UTC) - timedelta(seconds=5)
        await self.session.execute(
            text("UPDATE source_analyses SET lease_expires_at=:past WHERE id=:aid"),
            {"past": past_time, "aid": analysis.id},
        )
        await self.session.commit()

        # Delayed retry fires -> Worker B executes successfully
        analyzer = MockAnalyzer()
        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(analyzer),
        )
        result = await use_case.execute(analysis.id)
        assert result["status"] == "completed"

        # Verify exactly once result persistence
        result_rows = (
            await self.session.execute(
                select(OrmSourceAnalysisResult).where(OrmSourceAnalysisResult.source_analysis_id == analysis.id)
            )
        ).scalars().all()
        assert len(result_rows) == 1

    # -------------------------------------------------------------------------
    # 5. SourceTask ACTIVE_LEASE Redelivery Audit & Test
    # -------------------------------------------------------------------------
    async def test_source_task_active_lease_retry_on_redelivery(self) -> None:
        """SourceTask redelivery during active lease must schedule delayed retry instead of ACK."""
        token = uuid4()
        claim = await self.task_repo.claim_task_explicit(self.task1.id, claim_token=token)
        assert claim.status == ClaimStatus.CLAIMED

        # Worker B receives redelivered task
        with patch.object(process_source_task, "retry", side_effect=Retry("Delayed retry scheduled")) as mock_retry:
            with patch("app.workers.source_tasks.run_source_task_worker") as mock_run_source_task:
                mock_run_source_task.return_value = {
                    "status": "active_lease",
                    "task_id": str(self.task1.id),
                    "lease_expires_at": claim.lease_expires_at.isoformat(),
                }
                with pytest.raises(Retry):
                    process_source_task(str(self.task1.id))

                mock_retry.assert_called_once()
                _, kwargs = mock_retry.call_args
                assert kwargs["countdown"] >= 50
                assert kwargs.get("max_retries") == 10

    # -------------------------------------------------------------------------
    # 6. Same-Run Analyses Share Frozen Config (Critical)
    # -------------------------------------------------------------------------
    async def test_same_run_analyses_share_frozen_config(self) -> None:
        """When platform settings mutate midway through a run, all sources in that run

        MUST continue using the identical frozen RunAnalysisSpec snapshot.
        """
        # Step 1: Source 1 completes under initial settings (Provider A)
        with patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider") as mock_provider:
            mock_runtime_a = MagicMock()
            mock_runtime_a.provider_id = "openai"
            mock_runtime_a.model = "gpt-4o-mini"
            mock_provider.return_value = mock_runtime_a

            await self.handoff_adapter.ensure_and_dispatch(
                source_task_id=self.task1.id,
                run_id=self.run.id,
                source_id=self.source1.id,
                topic="energy",
            )

        analysis1 = await self.analysis_repo.get_by_source_task_id(self.task1.id)
        assert analysis1 is not None
        assert analysis1.analysis_spec.model_provider == "openai"
        assert analysis1.analysis_spec.model_name == "gpt-4o-mini"

        # Step 2: Global runtime settings change to Provider B midway through run
        with patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider") as mock_provider:
            mock_runtime_b = MagicMock()
            mock_runtime_b.provider_id = "anthropic"
            mock_runtime_b.model = "claude-3-haiku"
            mock_provider.return_value = mock_runtime_b

            # Source 2 completes later in the same run
            await self.handoff_adapter.ensure_and_dispatch(
                source_task_id=self.task2.id,
                run_id=self.run.id,
                source_id=self.source2.id,
                topic="energy",
            )

        analysis2 = await self.analysis_repo.get_by_source_task_id(self.task2.id)
        assert analysis2 is not None

        # CRITICAL ASSERTION: Analysis 2 used the FROZEN config from RunAnalysisSpec, NOT Provider B!
        assert analysis2.analysis_spec.model_provider == "openai"
        assert analysis2.analysis_spec.model_name == "gpt-4o-mini"
        assert analysis1.analysis_spec.model_provider == analysis2.analysis_spec.model_provider
        assert analysis1.analysis_spec.model_name == analysis2.analysis_spec.model_name
        assert analysis1.analysis_spec.prompt_version == analysis2.analysis_spec.prompt_version

    # -------------------------------------------------------------------------
    # 7. Global Config Change Only Affects Next Run
    # -------------------------------------------------------------------------
    async def test_global_config_change_only_affects_next_run(self) -> None:
        """Global config changes only take effect when a NEW MonitoringRun is created."""
        # Run 1 uses Provider A
        with patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider") as mock_provider:
            mock_a = MagicMock()
            mock_a.provider_id = "openai"
            mock_a.model = "gpt-4o"
            mock_provider.return_value = mock_a

            await self.handoff_adapter.ensure_and_dispatch(
                source_task_id=self.task1.id,
                run_id=self.run.id,
                source_id=self.source1.id,
            )

        # Settings change to Provider B and Run 2 starts on a new job
        user = await self.session.scalar(select(User).limit(1))
        job2 = MonitoringJob(
            id=uuid4(),
            topic="job 2 topic",
            date_from_jalali="1405/01/01",
            date_to_jalali="1405/01/10",
            date_from_gregorian=date(2026, 1, 1),
            date_to_gregorian=date(2026, 1, 10),
            requested_by=user.id,
            bot_identity_id=self.job.bot_identity_id,
            status=JobStatus.CRAWLING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(job2)
        await self.session.flush()

        run2 = OrmMonitoringRun(
            id=uuid4(),
            monitoring_job_id=job2.id,
            execution_mode="distributed",
            status=MonitoringRunStatus.RUNNING,
            expected_sources=1,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(run2)
        await self.session.flush()

        task_run2 = OrmSourceTask(
            id=uuid4(),
            run_id=run2.id,
            source_id=self.source2.id,
            ordinal=1,
            status=SourceTaskStatus.PENDING,
            attempt_count=0,
            execution_spec={
                "source_id": str(self.source2.id),
                "topic": job2.topic,
                "date_from": "2026-01-01",
                "date_to": "2026-01-10",
                "crawl_config": {},
                "base_url": self.source2.base_url,
            },
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self.session.add(task_run2)
        await self.session.commit()

        with patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider") as mock_provider:
            mock_b = MagicMock()
            mock_b.provider_id = "deepseek"
            mock_b.model = "deepseek-chat"
            mock_provider.return_value = mock_b

            await self.handoff_adapter.ensure_and_dispatch(
                source_task_id=task_run2.id,
                run_id=run2.id,
                source_id=self.source2.id,
            )

        analysis1 = await self.analysis_repo.get_by_source_task_id(self.task1.id)
        analysis2 = await self.analysis_repo.get_by_source_task_id(task_run2.id)

        assert analysis1.analysis_spec.model_provider == "openai"
        assert analysis2.analysis_spec.model_provider == "deepseek"

    # -------------------------------------------------------------------------
    # 8. RunAnalysisSpec Initialization is Idempotent
    # -------------------------------------------------------------------------
    async def test_run_analysis_spec_initialization_is_idempotent(self) -> None:
        """Calling ensure_run_analysis_spec multiple times for the same run produces only one row."""
        spec1 = await self.handoff_adapter.ensure_run_analysis_spec(self.run.id)
        spec2 = await self.handoff_adapter.ensure_run_analysis_spec(self.run.id)

        assert spec1.id == spec2.id
        assert spec1.spec_digest == spec2.spec_digest

        rows = (
            await self.session.execute(
                select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id == self.run.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 9. Concurrent RunSpec Initialization Creates One Row
    # -------------------------------------------------------------------------
    async def test_concurrent_run_spec_initialization_creates_one_row(self) -> None:
        """Concurrent calls to ensure_run_analysis_spec for the same run do not duplicate rows."""
        spec1, spec2 = await asyncio.gather(
            self.handoff_adapter.ensure_run_analysis_spec(self.run.id),
            self.handoff_adapter.ensure_run_analysis_spec(self.run.id),
        )

        assert spec1.spec == spec2.spec
        rows = (
            await self.session.execute(
                select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id == self.run.id)
            )
        ).scalars().all()
        assert len(rows) == 1

    # -------------------------------------------------------------------------
    # 10. RunSpec Contains No Secrets
    # -------------------------------------------------------------------------
    async def test_run_spec_contains_no_secrets(self) -> None:
        """RunAnalysisSpec strictly excludes credentials and secret patterns."""
        forbidden_payloads = [
            {"model_provider": "openai", "api_key": "sk-1234567890"},
            {"model_provider": "openai", "auth_token": "bearer secret"},
            {"model_provider": "openai", "custom_secret": "my-secret"},
        ]
        for payload in forbidden_payloads:
            with pytest.raises(ValueError, match="forbidden"):
                validate_run_spec_secrets(payload)

    # -------------------------------------------------------------------------
    # 11. Existing Transient Retry Preserved
    # -------------------------------------------------------------------------
    async def test_existing_transient_retry_preserved(self) -> None:
        """Transient errors release the claim and raise TransientAnalysisError for Celery retry."""
        spec = SourceAnalysisSpec(topic="test")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        flaky = FlakyAnalyzer(transient_attempts=1)
        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(flaky),
        )

        # Attempt 1: raises TransientAnalysisError and releases claim back to PENDING
        with pytest.raises(TransientAnalysisError):
            await use_case.execute(analysis.id)

        refreshed = await self.analysis_repo.get_by_id(analysis.id)
        assert refreshed.status == SourceAnalysisStatus.PENDING
        assert refreshed.claim_token is None

        # Attempt 2: retry succeeds
        res = await use_case.execute(analysis.id)
        assert res["status"] == "completed"

    # -------------------------------------------------------------------------
    # 12. Existing Permanent Failure Preserved
    # -------------------------------------------------------------------------
    async def test_existing_permanent_failure_preserved(self) -> None:
        """Permanent analysis failure sets status to FAILED without retry."""
        spec = SourceAnalysisSpec(topic="test")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        class BrokenAnalyzer:
            async def analyze_source(self, *args, **kwargs):
                raise ValueError("Permanent schema failure: invalid JSON")

        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(BrokenAnalyzer()),
        )
        outcome = await use_case.execute(analysis.id)
        assert outcome["status"] == "failed"

        refreshed = await self.analysis_repo.get_by_id(analysis.id)
        assert refreshed.status == SourceAnalysisStatus.FAILED

    # -------------------------------------------------------------------------
    # 13. Existing Streaming Proof Preserved
    # -------------------------------------------------------------------------
    async def test_existing_streaming_proof_preserved(self) -> None:
        """Source A gains a durable analysis obligation without waiting for Source B."""
        await self.handoff_adapter.ensure_and_dispatch(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
        )
        analysis = await self.session.scalar(select(OrmSourceAnalysis).where(
            OrmSourceAnalysis.source_task_id == self.task1.id))
        successor = await self.session.scalar(select(IntegrationOutbox).where(
            IntegrationOutbox.message_key == f"source-analysis:{self.task1.id}"))
        other = await self.session.scalar(select(OrmSourceAnalysis).where(
            OrmSourceAnalysis.source_task_id == self.task2.id))
        assert analysis is not None and successor is not None
        assert successor.payload_json["source_analysis_id"] == str(analysis.id)
        assert other is None
        assert self.dispatcher.dispatched_ids == []

    # -------------------------------------------------------------------------
    # 14. Existing Document Isolation Preserved
    # -------------------------------------------------------------------------
    async def test_existing_document_isolation_preserved(self) -> None:
        """Documents of Source A are strictly isolated from Source B."""
        doc1 = SourceTaskDocument(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            url="https://alpha.example.org/1",
            content_hash="h1",
            clean_text="Alpha content",
        )
        self.session.add(doc1)
        await self.session.commit()

        docs = await self.doc_repo.get_documents_for_task(self.task1.id)
        assert len(docs) == 1
        assert docs[0].url == "https://alpha.example.org/1"

    # -------------------------------------------------------------------------
    # 15. Existing Stale Result Fencing Preserved
    # -------------------------------------------------------------------------
    async def test_existing_stale_result_fencing_preserved(self) -> None:
        """Stale worker attempting to persist results after lease loss is rejected by fence."""
        spec = SourceAnalysisSpec(topic="test")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        # Worker A claims
        worker_a_token = uuid4()
        claim_a = await self.analysis_repo.claim_explicit(analysis.id, worker_token=worker_a_token)
        assert claim_a.status == AnalysisClaimStatus.CLAIMED

        # Expire lease in DB so Worker B can reclaim
        past = datetime.now(UTC) - timedelta(seconds=10)
        await self.session.execute(
            text("UPDATE source_analyses SET lease_expires_at=:past WHERE id=:aid"),
            {"past": past, "aid": analysis.id},
        )
        await self.session.commit()

        # Worker B reclaims after expiry
        worker_b_token = uuid4()
        claim_b = await self.analysis_repo.claim_explicit(analysis.id, worker_token=worker_b_token)
        assert claim_b.status == AnalysisClaimStatus.CLAIMED

        # Worker A attempts to save results using stale worker_a_token -> rejected by fence
        dummy_result = SourceAnalysisResultData(
            schema_version="1.0",
            input_digest="d",
            summary="Stale summary",
            findings=[],
            evidence_map={},
            raw_result={},
        )
        saved = await self.result_repo.save_result_fenced(analysis.id, worker_a_token, dummy_result)
        assert saved is False

    # -------------------------------------------------------------------------
    # 16. Existing No Fan-in Preserved
    # -------------------------------------------------------------------------
    async def test_existing_no_fan_in_preserved(self) -> None:
        """Completing source analyses leaves MonitoringRun in RUNNING status without fan-in finalization."""
        spec = SourceAnalysisSpec(topic="test")
        analysis = await self.analysis_repo.ensure_for_source_task(
            source_task_id=self.task1.id,
            run_id=self.run.id,
            source_id=self.source1.id,
            spec=spec,
        )
        await self.session.commit()

        analyzer = MockAnalyzer()
        use_case = ExecuteSourceAnalysisUseCase(
            analysis_repository=self.analysis_repo,
            result_repository=self.result_repo,
            document_reader=self.doc_repo,
            analyze_use_case=AnalyzeSourceUseCase(analyzer),
        )
        await use_case.execute(analysis.id)

        # Assert MonitoringRun is still RUNNING
        run_record = await self.run_repo.get_by_id(self.run.id)
        assert run_record.status == MonitoringRunStatus.RUNNING
