"""Publisher adapter for MonitoringAnalysisReady event."""

from __future__ import annotations

import logging
from uuid import UUID

from app.modules.analysis.ports.run_aggregation import (
    MonitoringAnalysisReadyPublisherPort,
)

logger = logging.getLogger("rasad.analysis.publisher")


class CeleryMonitoringAnalysisReadyPublisher(MonitoringAnalysisReadyPublisherPort):
    """Dispatches MonitoringAnalysisReady signals to the monitoring.control queue."""

    def __init__(self, celery_app: object | None = None) -> None:
        self._celery_app = celery_app

    def _get_app(self) -> object:
        if self._celery_app is not None:
            return self._celery_app
        from app.workers.celery_app import celery_app
        return celery_app

    def publish_analysis_ready(
        self,
        run_id: UUID,
        aggregation_id: UUID,
        result_id: UUID,
    ) -> None:
        try:
            app = self._get_app()
            payload = {
                "event": "analysis_ready",
                "run_id": str(run_id),
                "aggregation_id": str(aggregation_id),
                "result_id": str(result_id),
            }
            getattr(app, "send_task")(
                "monitoring.process_control_event",
                args=[payload],
                queue="monitoring.control",
            )
            logger.info("Published analysis_ready for run %s (aggregation %s)", run_id, aggregation_id)
        except Exception as exc:
            logger.error("Failed to publish analysis_ready for run %s: %s", run_id, exc)
