"""Source-level failure logging and recording."""

import logging
from typing import Protocol
from uuid import UUID

logger = logging.getLogger("rasad.worker.monitoring")


class SourceFailureLoggerPort(Protocol):
    async def log_source_failure(
        self,
        job_id: UUID,
        source_id: UUID,
        source_title: str,
        error_message: str,
        error_type: str,
    ) -> None: ...


class SourceFailureRecorder:
    """Record isolated failures for individual sources so monitoring continues."""

    def __init__(self, failure_logger: SourceFailureLoggerPort) -> None:
        self._failure_logger = failure_logger

    async def record_failure(
        self,
        job_id: UUID,
        source_id: UUID,
        source_title: str,
        error: Exception,
    ) -> None:
        message = str(error).strip() or f"{error.__class__.__name__}: unexpected error without a message"
        try:
            await self._failure_logger.log_source_failure(
                job_id=job_id,
                source_id=source_id,
                source_title=source_title,
                error_message=message,
                error_type=error.__class__.__name__,
            )
        except Exception:
            logger.exception(
                "Failed to persist source crawl failure",
                extra={"job_id": str(job_id), "source_id": str(source_id)},
            )
