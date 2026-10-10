"""Composition root for the monitoring control worker workflow."""

from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool

from app.core.config import settings

control_worker_engine = create_async_engine(settings.database_url, poolclass=NullPool)
ControlWorkerSessionFactory = async_sessionmaker(
    control_worker_engine, expire_on_commit=False, class_=AsyncSession
)


def run_control_event_worker(payload: dict[str, object], outbox_id: UUID | None = None):
    from app.modules.analysis.infrastructure.handoff_aggregation_adapter import (
        RunAggregationHandoffAdapter,
    )
    from app.modules.analysis.infrastructure.run_aggregation_repository import (
        SqlAlchemyRunAggregationRepository,
    )
    from app.modules.monitoring.application.evaluate_completion import (
        EvaluateRunCompletionUseCase,
    )
    from app.modules.monitoring.application.control_event import ProcessMonitoringControlEvent
    from app.modules.monitoring.infrastructure.completion_tracker_repository import (
        SqlAlchemyRunCompletionTrackerRepository,
    )
    from app.modules.monitoring.infrastructure.postgres_deadline_policy import PostgresDeadlinePolicy
    from app.modules.monitoring.infrastructure.execution_repository import (
        SqlAlchemyMonitoringRunRepository,
    )
    from app.modules.monitoring.ports.completion import TransientControlInfrastructureError

    tracker_repo = SqlAlchemyRunCompletionTrackerRepository(ControlWorkerSessionFactory)
    run_repo = SqlAlchemyMonitoringRunRepository(ControlWorkerSessionFactory)
    agg_repo = SqlAlchemyRunAggregationRepository(ControlWorkerSessionFactory)
    handoff = RunAggregationHandoffAdapter(agg_repo)

    use_case = EvaluateRunCompletionUseCase(
        tracker_repository=tracker_repo,
        run_repository=run_repo,
        aggregation_handoff=handoff,
        deadline_scheduler=None,
        deadline_policy=PostgresDeadlinePolicy(),
    )

    processor = ProcessMonitoringControlEvent(use_case, tracker_repo, run_repo)
    try:
        return asyncio.run(processor.execute(payload, outbox_id=outbox_id))
    except SQLAlchemyError as exc:
        raise TransientControlInfrastructureError("Temporary control database failure") from exc
