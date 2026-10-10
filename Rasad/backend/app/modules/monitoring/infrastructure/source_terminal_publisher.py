"""Publisher adapter for source terminal events to monitoring.control queue."""

from __future__ import annotations

from datetime import datetime
import logging
from uuid import UUID

from app.modules.monitoring.ports.completion import SourceTerminalPublisherPort

logger = logging.getLogger("rasad.monitoring.publisher")


class CelerySourceTerminalPublisher(SourceTerminalPublisherPort):
    """Dispatches source terminal signals to the monitoring.control queue."""

    def __init__(self, celery_app: object | None = None) -> None:
        self._celery_app = celery_app

    def _get_app(self) -> object:
        if self._celery_app is not None:
            return self._celery_app
        from app.workers.celery_app import celery_app
        return celery_app

    def publish_source_terminal(
        self,
        run_id: UUID,
        source_task_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> None:
        try:
            app = self._get_app()
            payload = {
                "event": "source_terminal",
                "run_id": str(run_id),
                "source_task_id": str(source_task_id),
                "outcome": outcome,
                "occurred_at": occurred_at.isoformat(),
            }
            getattr(app, "send_task")(
                "monitoring.process_control_event",
                args=[payload],
                queue="monitoring.control",
            )
            logger.info("Published source_terminal for task %s (run %s)", source_task_id, run_id)
        except Exception as exc:
            logger.error(
                "Failed to publish source_terminal for task %s (run %s): %s",
                source_task_id,
                run_id,
                exc,
            )
