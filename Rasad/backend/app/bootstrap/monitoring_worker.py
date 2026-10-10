"""Composition root for the durable monitoring worker workflow."""

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.bootstrap.analysis import provide_analysis_job_coordinator
from app.bootstrap.distributed_analysis import provide_distributed_analysis_freezer
from app.core.config import settings
from app.modules.documents.infrastructure import SqlAlchemyDocumentRepository
from app.modules.documents.infrastructure.record_mapper import to_prepared_document_record
from app.modules.monitoring.api import ProcessMonitoringJob
from app.modules.monitoring.infrastructure.pipeline_steps import MonitoringPipelineSteps
from app.modules.monitoring.infrastructure.repository import SqlAlchemyMonitoringJobRepository

# Dedicated engine with NullPool for worker tasks to avoid cross-loop/cross-fork connection sharing
worker_engine = create_async_engine(settings.database_url, poolclass=NullPool)
WorkerSessionFactory = async_sessionmaker(worker_engine, expire_on_commit=False, class_=AsyncSession)


def process_monitoring_job(job_id: str) -> dict[str, object]:
    workflow = ProcessMonitoringJob(
        MonitoringPipelineSteps(
            session_factory=WorkerSessionFactory,
            monitoring_repositories=SqlAlchemyMonitoringJobRepository,
            document_repositories=SqlAlchemyDocumentRepository,
            analysis_coordinators=provide_analysis_job_coordinator,
            map_document=to_prepared_document_record,
            dispose_database=worker_engine.dispose,
            run_spec_freezer=provide_distributed_analysis_freezer(WorkerSessionFactory),
        )
    )
    return asyncio.run(workflow.execute(UUID(job_id)))


def run_source_task_worker(source_task_id: str) -> dict[str, object]:
    from app.modules.monitoring.infrastructure.crawl_checkpoint import FencedCrawlCheckpoint
    from app.modules.monitoring.application.execute_source_task import ExecuteSourceTaskUseCase
    from app.modules.monitoring.application.source_failure import SourceFailureRecorder
    from app.modules.monitoring.infrastructure.execution_repository import (
        SqlAlchemyMonitoringRunRepository,
        SqlAlchemySourceTaskRepository,
    )
    from app.modules.monitoring.infrastructure.pipeline_adapters import (
        SqlAlchemyPipelinePersistenceAdapter,
    )
    from app.modules.monitoring.infrastructure.source_execution_engine import (
        SourceExecutionEngine,
    )
    from app.modules.monitoring.infrastructure.source_result_persister import (
        SourceResultPersister,
    )

    persistence = SqlAlchemyPipelinePersistenceAdapter(
        WorkerSessionFactory,
        SqlAlchemyMonitoringJobRepository,
        SqlAlchemyDocumentRepository,
    )
    failure_recorder = SourceFailureRecorder(persistence)

    async def _execute() -> dict[str, object]:
        async with WorkerSessionFactory() as session:
            task_repo = SqlAlchemySourceTaskRepository(session)
            run_repo = SqlAlchemyMonitoringRunRepository(session)
            engine_inst = SourceExecutionEngine()
            persister = SourceResultPersister(
                session_factory=WorkerSessionFactory,
                monitoring_repositories=SqlAlchemyMonitoringJobRepository,
                document_repositories=SqlAlchemyDocumentRepository,
                map_document=to_prepared_document_record,
                failure_recorder=failure_recorder,
            )
            use_case = ExecuteSourceTaskUseCase(
                task_repository=task_repo,
                run_repository=run_repo,
                execution_engine=engine_inst,
                result_persister=persister,
                checkpoint_factory=lambda task_id, token, spec: FencedCrawlCheckpoint(WorkerSessionFactory, task_id, token, spec),
            )
            return await use_case.execute(UUID(source_task_id))

    return asyncio.run(_execute())
