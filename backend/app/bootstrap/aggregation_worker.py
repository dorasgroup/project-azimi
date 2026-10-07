"""Composition root for the run-level aggregation worker workflow."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

aggregation_worker_engine = create_async_engine(settings.database_url, poolclass=NullPool)
AggregationWorkerSessionFactory = async_sessionmaker(
    aggregation_worker_engine, expire_on_commit=False, class_=AsyncSession
)


def run_aggregation_finalization(aggregation_id: UUID, outbox_id: UUID | None) -> dict[str, object]:
    from app.modules.analysis.infrastructure.aggregation_terminal_reader import AggregationTerminalReader
    from app.modules.monitoring.application.finalize_aggregation import FinalizeMonitoringAggregation
    from app.modules.monitoring.infrastructure.aggregation_finalization_repository import MonitoringAggregationFinalizationRepository
    return asyncio.run(FinalizeMonitoringAggregation(
        AggregationTerminalReader(AggregationWorkerSessionFactory),
        MonitoringAggregationFinalizationRepository(AggregationWorkerSessionFactory),
    ).execute(aggregation_id, outbox_id))


def run_aggregation_handoff(run_id_str: str, consumed_outbox_id: UUID | None = None) -> None:
    from app.models.completion_tracker import OrmRunCompletionTracker
    from app.models.monitoring_run import MonitoringRun
    from app.modules.analysis.infrastructure.run_aggregation_repository import SqlAlchemyRunAggregationRepository

    async def _execute() -> None:
        run_id = UUID(run_id_str)
        async with AggregationWorkerSessionFactory() as session:
            tracker = await session.scalar(select(OrmRunCompletionTracker).where(OrmRunCompletionTracker.run_id == run_id))
            run = await session.get(MonitoringRun, run_id)
            if tracker is None or run is None:
                raise ValueError("Aggregation handoff missing durable run/tracker")
            if tracker.status not in ("ready_all_terminal", "ready_deadline", "aggregation_dispatched"):
                if consumed_outbox_id is not None:
                    from app.shared.integration.consumer_ack import acknowledge_in_session
                    await acknowledge_in_session(session, consumed_outbox_id)
                    await session.commit()
                return
            metadata = {
                "expected_sources": run.expected_sources,
                "successful_sources": run.successful_sources,
                "failed_sources": run.failed_sources,
                "cancelled_sources": run.cancelled_sources,
                "analysis_successful": tracker.analysis_successful_count,
                "analysis_failed": tracker.analysis_failed_count,
                "analysis_cancelled": tracker.analysis_cancelled_count,
            }
            repository = SqlAlchemyRunAggregationRepository(AggregationWorkerSessionFactory)
            await repository.ensure_aggregation_and_manifest(
                run_id=run_id,
                readiness_reason=tracker.readiness_reason or "ready",
                cutoff_at=tracker.cutoff_at,
                completion_metadata=metadata,
                consumed_outbox_id=consumed_outbox_id,
            )

    asyncio.run(_execute())


def run_aggregation_worker(aggregation_id_str: str) -> dict[str, object]:
    from app.modules.analysis.application.aggregate_analysis_use_case import (
        ExecuteRunAggregationUseCase,
    )
    from app.modules.analysis.infrastructure.run_aggregation_repository import (
        SqlAlchemyRunAggregationRepository,
    )
    from app.modules.analysis.infrastructure.run_aggregator_adapter import (
        RunAggregatorAdapter,
    )
    from app.modules.analysis.infrastructure.run_analysis_spec_repository import (
        SqlAlchemyRunAnalysisSpecRepository,
    )

    agg_repo = SqlAlchemyRunAggregationRepository(AggregationWorkerSessionFactory)
    spec_repo = SqlAlchemyRunAnalysisSpecRepository(AggregationWorkerSessionFactory)
    from app.bootstrap.analysis import provide_frozen_model_gateway
    aggregator = RunAggregatorAdapter(pinned_gateway_factory=provide_frozen_model_gateway)

    use_case = ExecuteRunAggregationUseCase(
        aggregation_repository=agg_repo,
        run_spec_repository=spec_repo,
        aggregator=aggregator,
        ready_publisher=None,
    )

    return asyncio.run(use_case.execute(UUID(aggregation_id_str)))
