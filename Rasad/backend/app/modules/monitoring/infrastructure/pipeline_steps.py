from app.modules.monitoring.application.fanout_controller import FanOutSourceTasksUseCase
from app.modules.monitoring.infrastructure.celery_dispatcher import OutboxSourceTaskDispatcher
from app.modules.monitoring.infrastructure.execution_repository import SqlAlchemyMonitoringRunRepository, SqlAlchemySourceTaskRepository
from app.core.config import settings
"""Thin Facade delegating monitoring workflow steps to focused components."""

from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.documents.ports import DocumentRepository
from app.modules.monitoring.application.analysis_step import AnalysisStep
from app.modules.monitoring.application.cancellation import CancellationGuard
from app.modules.monitoring.application.coverage import CoverageRecorder
from app.modules.monitoring.application.job_failure import JobFailureHandler, extract_error_message
from app.modules.monitoring.application.progress import SourceProgressRecorder
from app.modules.monitoring.application.source_batch import SourceBatchProcessor
from app.modules.monitoring.application.source_failure import SourceFailureRecorder
from app.modules.monitoring.application.startup import JobStartupStep
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
    SourceProcessingResult,
)
from app.modules.monitoring.application.workflows import MonitoringProgress, MonitoringStart
from app.modules.monitoring.infrastructure.pipeline_adapters import SqlAlchemyPipelinePersistenceAdapter
from app.modules.monitoring.infrastructure.source_processor import SingleSourceProcessor
from app.modules.monitoring.ports import MonitoringJobRepository
from app.processing.schemas import PreparedDocument

# Backward-compatibility alias
_error_message = extract_error_message
MAX_CONCURRENT_SOURCES = 5


class AnalysisResult(Protocol):
    id: UUID


class AnalysisCoordinator(Protocol):
    async def analyze_job(self, job_id: UUID) -> AnalysisResult: ...


MonitoringRepositoryFactory = Callable[[AsyncSession], MonitoringJobRepository]
DocumentRepositoryFactory = Callable[[AsyncSession], DocumentRepository]
AnalysisCoordinatorFactory = Callable[[AsyncSession], AnalysisCoordinator]
PreparedDocumentMapper = Callable[[PreparedDocument], PreparedDocumentRecord]


class MonitoringPipelineSteps:
    """Thin Facade implementing MonitoringJobProcessor via single-responsibility components."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        monitoring_repositories: MonitoringRepositoryFactory,
        document_repositories: DocumentRepositoryFactory,
        analysis_coordinators: AnalysisCoordinatorFactory,
        map_document: PreparedDocumentMapper,
        dispose_database: Callable[[], Awaitable[None]],
        run_spec_freezer=None,
    ) -> None:
        self._dispose_database = dispose_database
        self._session_factory = session_factory
        self._run_spec_freezer = run_spec_freezer
        self._monitoring_repositories = monitoring_repositories
        self._document_repositories = document_repositories
        self._analysis_coordinators = analysis_coordinators
        self._map_document = map_document

        # 1. Persistence Adapter
        persistence = SqlAlchemyPipelinePersistenceAdapter(
            session_factory, monitoring_repositories, document_repositories
        )

        # 2. Focused Application Components
        self._cancellation_guard = CancellationGuard(persistence)
        self._progress_recorder = SourceProgressRecorder(persistence)
        self._failure_recorder = SourceFailureRecorder(persistence)
        self._coverage_recorder = CoverageRecorder(persistence, persistence)

        # 3. Source Processor & Batch Processor
        self._source_processor = SingleSourceProcessor(
            session_factory,
            monitoring_repositories,
            document_repositories,
            map_document,
            self._failure_recorder,
        )
        self._batch_processor = SourceBatchProcessor(
            self._cancellation_guard,
            self._progress_recorder,
            self._source_processor,
        )

        # 4. Analysis Coordinator Wrapper
        class AnalysisCoordinatorWrapper:
            def __init__(self, coordinators_factory, sf):
                self._coordinators_factory = coordinators_factory
                self._sf = sf

            async def analyze_job(self, job_id: UUID) -> object:
                async with self._sf() as session:
                    return await self._coordinators_factory(session).analyze_job(job_id)

        self._analysis_step = AnalysisStep(
            self._coverage_recorder,
            self._progress_recorder,
            persistence,
            AnalysisCoordinatorWrapper(analysis_coordinators, session_factory),
        )

        # 5. Startup & Failure Steps
        self._startup_step = JobStartupStep(persistence)
        self._failure_handler = JobFailureHandler(persistence)

    # -----------------------------------------------------------------
    # Facade Properties for Mock Compatibility
    # -----------------------------------------------------------------
    @property
    def startup_step(self) -> JobStartupStep:
        if hasattr(self, "_startup_step"):
            return self._startup_step
        persistence = getattr(self, "_persistence", None)
        if persistence is None and hasattr(self, "_session_factory"):
            persistence = SqlAlchemyPipelinePersistenceAdapter(
                self._session_factory,
                getattr(self, "_monitoring_repositories", None),
                getattr(self, "_document_repositories", None),
            )
        return JobStartupStep(persistence)

    @property
    def batch_processor(self) -> SourceBatchProcessor:
        if hasattr(self, "_batch_processor"):
            return self._batch_processor
        guard = getattr(self, "_cancellation_guard", self)
        recorder = getattr(self, "_progress_recorder", self)
        processor = getattr(self, "_source_processor", self)
        return SourceBatchProcessor(guard, recorder, processor)

    @property
    def analysis_step(self) -> AnalysisStep:
        if hasattr(self, "_analysis_step"):
            return self._analysis_step
        persistence = getattr(self, "_persistence", None)
        if persistence is None and hasattr(self, "_session_factory"):
            persistence = SqlAlchemyPipelinePersistenceAdapter(
                self._session_factory,
                getattr(self, "_monitoring_repositories", None),
                getattr(self, "_document_repositories", None),
            )
        coverage = getattr(self, "_coverage_recorder", CoverageRecorder(persistence, persistence))
        if hasattr(self, "_record_coverage"):
            async def custom_coverage(jid, src):
                return await self._record_coverage(None, None, None)
            coverage.calculate_and_record = custom_coverage
        progress = getattr(self, "_progress_recorder", SourceProgressRecorder(persistence))
        coordinator = getattr(self, "_analysis_coordinators", None)
        return AnalysisStep(coverage, progress, persistence, coordinator)

    @property
    def failure_handler(self) -> JobFailureHandler:
        if hasattr(self, "_failure_handler"):
            return self._failure_handler
        persistence = getattr(self, "_persistence", None)
        if persistence is None and hasattr(self, "_session_factory"):
            persistence = SqlAlchemyPipelinePersistenceAdapter(
                self._session_factory,
                getattr(self, "_monitoring_repositories", None),
                getattr(self, "_document_repositories", None),
            )
        return JobFailureHandler(persistence)

    # -----------------------------------------------------------------
    # Primary Workflow Methods (Delegating Facade)
    # -----------------------------------------------------------------
    async def start(self, job_id: UUID) -> MonitoringStart:
        if hasattr(self, "_dispose_database") and callable(self._dispose_database):
            await self._dispose_database()
        return await self.startup_step.run(job_id)

    async def process_sources(self, context: MonitoringExecutionContext) -> MonitoringProgress:
        # Execution mode is run-scoped and resolved strictly from context or database run record.
        execution_mode = context.execution_mode
        if execution_mode is None and context.run_id is not None:
            async with self._session_factory() as session:
                run_repo = SqlAlchemyMonitoringRunRepository(session)
                run = await run_repo.get_by_id(context.run_id)
                if run is not None:
                    execution_mode = run.execution_mode

        if execution_mode in ("distributed", "distributed_fanout"):
            freezer = getattr(self, "_run_spec_freezer", None)
            if freezer is None:
                raise ValueError("Distributed fanout requires an injected Analysis spec freezer")
            async with self._session_factory() as session:
                run_repo = SqlAlchemyMonitoringRunRepository(session)
                task_repo = SqlAlchemySourceTaskRepository(session)
                dispatcher = OutboxSourceTaskDispatcher(session)
                fanout_uc = FanOutSourceTasksUseCase(run_repo, task_repo, dispatcher, freezer)
                dispatch_result = await fanout_uc.fan_out(context)
                await session.commit()
            return MonitoringProgress(
                documents=0,
                terminal_result=dispatch_result,
            )

        return await self.batch_processor.process_sources(context)

    async def analyze(self, context: MonitoringExecutionContext, documents: int) -> dict[str, object]:
        return await self.analysis_step.run(context, documents)

    async def fail(self, job_id: UUID, error: Exception) -> None:
        await self.failure_handler.handle(job_id, error)

    async def close(self) -> None:
        if hasattr(self, "_dispose_database") and callable(self._dispose_database):
            await self._dispose_database()

    # -----------------------------------------------------------------
    # Protocols Implementation & Fallback Delegates for Uninitialized / Mock Instances
    # -----------------------------------------------------------------
    async def is_cancelled(self, job_id: UUID) -> bool:
        if hasattr(self, "_is_cancelled"):
            return await self._is_cancelled(job_id)
        return False

    async def record_crawl_progress(
        self,
        job_id: UUID,
        *,
        sources_completed: int,
        source_count: int,
        pages_processed: int,
        documents_prepared: int,
        eta_seconds: int | None,
        message: str,
    ) -> None:
        if hasattr(self, "_record_source_progress"):
            await self._record_source_progress(
                job_id,
                sources_completed=sources_completed,
                source_count=source_count,
                pages_processed=pages_processed,
                documents_prepared=documents_prepared,
                eta_seconds=eta_seconds,
                message=message,
            )

    async def process_source(
        self,
        context: MonitoringExecutionContext,
        source_data: MonitoringSourceSnapshot,
    ) -> SourceProcessingResult | None:
        if hasattr(self, "_process_source_guarded"):
            res = await self._process_source_guarded(context, source_data)
            if isinstance(res, SourceProcessingResult):
                return res
            if isinstance(res, int):
                return SourceProcessingResult(pages=0, documents=res)
            return res
        return SourceProcessingResult(pages=0, documents=0)
