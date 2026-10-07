"""Celery dispatcher adapter for source analysis tasks."""

from __future__ import annotations

import logging
from uuid import UUID

from app.modules.analysis.ports.source_analysis import SourceAnalysisDispatcherPort
from app.workers.celery_app import celery_app

logger = logging.getLogger("rasad.analysis.dispatcher")


class CelerySourceAnalysisDispatcher(SourceAnalysisDispatcherPort):
    """Dispatches per-source analysis tasks to Celery queue analysis.source."""

    def dispatch_analysis_task(self, source_analysis_id: UUID) -> str:
        logger.info("Dispatching source analysis task %s to queue analysis.source", source_analysis_id)
        result = celery_app.send_task(
            "analysis.process_source_analysis",
            args=[str(source_analysis_id)],
            queue="analysis.source",
        )
        return result.id
