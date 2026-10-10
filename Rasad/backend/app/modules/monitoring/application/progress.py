"""Progress recording and calculation for monitoring execution."""

from typing import Protocol
from uuid import UUID

from app.modules.monitoring.domain.progress import (
    MonitoringProgressSnapshot,
    encode_progress,
)


class ProgressPersistencePort(Protocol):
    async def save_progress(self, job_id: UUID, encoded_progress: str) -> None: ...


class SourceProgressRecorder:
    """Calculate and persist progress snapshots during crawl and analysis."""

    def __init__(self, persistence: ProgressPersistencePort) -> None:
        self._persistence = persistence

    async def record_crawl_progress(
        self,
        job_id: UUID,
        *,
        sources_completed: int,
        source_count: int,
        pages_processed: int,
        documents_prepared: int,
        eta_seconds: int | None,
        message: str,
    ) -> None:
        fraction = sources_completed / source_count if source_count else 1
        percent = round(5 + 65 * fraction)
        snapshot = MonitoringProgressSnapshot(
            stage="crawling",
            percent=percent,
            sources_completed=sources_completed,
            sources_total=source_count,
            pages_processed=pages_processed,
            documents_prepared=documents_prepared,
            eta_seconds=eta_seconds,
            message=message,
        )
        await self._persistence.save_progress(job_id, encode_progress(snapshot))

    async def record_analysis_progress(
        self,
        job_id: UUID,
        *,
        sources_total: int,
        sources_completed: int,
        pages_processed: int,
        documents_prepared: int,
        message: str = "خزش منابع تکمیل شد؛ تحلیل گزارش در حال انجام است",
    ) -> None:
        snapshot = MonitoringProgressSnapshot(
            stage="analyzing",
            percent=85,
            sources_completed=sources_completed,
            sources_total=sources_total,
            pages_processed=pages_processed,
            documents_prepared=documents_prepared,
            eta_seconds=None,
            message=message,
        )
        await self._persistence.save_progress(job_id, encode_progress(snapshot))
