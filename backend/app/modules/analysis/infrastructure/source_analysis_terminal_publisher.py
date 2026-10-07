"""Publisher adapter for source analysis terminal events to monitoring.control queue."""

from __future__ import annotations

from datetime import datetime
import logging
from uuid import UUID

from app.modules.analysis.ports.run_aggregation import (
    SourceAnalysisTerminalPublisherPort,
)

logger = logging.getLogger("rasad.analysis.publisher")


class CelerySourceAnalysisTerminalPublisher(SourceAnalysisTerminalPublisherPort):
    """Dispatches source analysis terminal signals to the monitoring.control queue."""

    def __init__(self, celery_app: object | None = None) -> None:
        self._celery_app = celery_app

    def _get_app(self) -> object:
        if self._celery_app is not None:
            return self._celery_app
        from app.workers.celery_app import celery_app
        return celery_app

    def publish_source_analysis_terminal(
        self,
        run_id: UUID,
        source_analysis_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> None:
        try:
            app = self._get_app()
            payload = {
                "event": "analysis_terminal",
                "run_id": str(run_id),
                "source_analysis_id": str(source_analysis_id),
                "outcome": outcome,
                "occurred_at": occurred_at.isoformat(),
            }
            getattr(app, "send_task")(
                "monitoring.process_control_event",
                args=[payload],
                queue="monitoring.control",
            )
            logger.info(
                "Published analysis_terminal for analysis %s (run %s)",
                source_analysis_id,
                run_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to publish analysis_terminal for analysis %s (run %s): %s",
                source_analysis_id,
                run_id,
                exc,
            )
