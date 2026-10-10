"""Handoff adapter implementing RunAggregationHandoffPort."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Mapping
from uuid import UUID

from app.modules.analysis.ports.run_aggregation import (
    RunAggregationRepositoryPort,
)
from app.modules.monitoring.ports.completion import (
    RunAggregationHandoffPort,
)

logger = logging.getLogger("rasad.analysis.handoff_aggregation")


class RunAggregationHandoffAdapter(RunAggregationHandoffPort):
    """Bridges Monitoring completion tracker to Analysis RunAggregation."""

    def __init__(
        self,
        aggregation_repository: RunAggregationRepositoryPort,
        celery_app: object | None = None,
    ) -> None:
        self._aggregation_repo = aggregation_repository
        self._celery_app = celery_app

    def _get_app(self) -> object:
        if self._celery_app is not None:
            return self._celery_app
        from app.workers.celery_app import celery_app
        return celery_app

    async def ensure_and_dispatch_aggregation(
        self,
        run_id: UUID,
        readiness_reason: str,
        cutoff_at: datetime | None,
        completion_metadata: Mapping[str, object],
    ) -> UUID | None:
        agg = await self._aggregation_repo.ensure_aggregation_and_manifest(
            run_id=run_id,
            readiness_reason=readiness_reason,
            cutoff_at=cutoff_at,
            completion_metadata=completion_metadata,
        )

        if agg is None:
            return None

        logger.info(
            "Ensured analysis.aggregate_run outbox intent for aggregation %s (run %s)",
            agg.id,
            run_id,
        )
        return agg.id
