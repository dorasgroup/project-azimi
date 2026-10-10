"""Composition root for the per-source analysis worker workflow."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

# Dedicated engine with NullPool for worker tasks to avoid cross-loop/cross-fork connection sharing
analysis_worker_engine = create_async_engine(settings.database_url, poolclass=NullPool)
AnalysisWorkerSessionFactory = async_sessionmaker(
    analysis_worker_engine, expire_on_commit=False, class_=AsyncSession
)


def run_source_analysis_handoff(source_task_id: str, consumed_outbox_id: UUID | None = None) -> None:
    from app.bootstrap.distributed_analysis import provide_distributed_analysis_freezer

    adapter = provide_distributed_analysis_freezer(AnalysisWorkerSessionFactory)
    asyncio.run(adapter.ensure_and_dispatch(UUID(source_task_id), consumed_outbox_id=consumed_outbox_id))


def run_source_analysis_worker(source_analysis_id: str) -> dict[str, object]:
    from app.bootstrap.analysis import provide_model_gateway, provide_frozen_model_gateway
    from app.modules.analysis.application.source_analysis_use_cases import (
        AnalyzeSourceUseCase,
        ExecuteSourceAnalysisUseCase,
    )
    from app.modules.analysis.infrastructure.single_source_analyzer import (
        SingleSourceAnalyzer,
    )
    from app.modules.analysis.infrastructure.source_analysis_repository import (
        SqlAlchemySourceAnalysisRepository,
    )
    from app.modules.analysis.infrastructure.source_analysis_result_repository import (
        SqlAlchemySourceAnalysisResultRepository,
        SqlAlchemySourceTaskDocumentRepository,
    )

    async def _execute() -> dict[str, object]:
        async with AnalysisWorkerSessionFactory() as session:
            analysis_repo = SqlAlchemySourceAnalysisRepository(session)
            result_repo = SqlAlchemySourceAnalysisResultRepository(session)
            doc_reader = SqlAlchemySourceTaskDocumentRepository(session)
            analyzer = SingleSourceAnalyzer(
                provide_model_gateway, pinned_gateway_factory=provide_frozen_model_gateway
            )
            analyze_use_case = AnalyzeSourceUseCase(analyzer)

            use_case = ExecuteSourceAnalysisUseCase(
                analysis_repository=analysis_repo,
                result_repository=result_repo,
                document_reader=doc_reader,
                analyze_use_case=analyze_use_case,
            )
            return await use_case.execute(UUID(source_analysis_id))

    return asyncio.run(_execute())
