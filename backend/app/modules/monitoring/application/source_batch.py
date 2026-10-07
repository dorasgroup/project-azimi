"""Bounded-concurrency batch processor for monitoring sources."""

import asyncio
from time import monotonic
from typing import Protocol

from app.modules.monitoring.application.cancellation import CancellationGuard
from app.modules.monitoring.application.progress import SourceProgressRecorder
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
    SourceProcessingResult,
)
from app.modules.monitoring.application.workflows import MonitoringProgress

MAX_CONCURRENT_SOURCES = 5


class SingleSourceProcessorPort(Protocol):
    async def process_source(
        self,
        context: MonitoringExecutionContext,
        source_data: MonitoringSourceSnapshot,
    ) -> SourceProcessingResult | None: ...


class SourceBatchProcessor:
    """Iterate and process monitoring sources in bounded concurrent batches."""

    def __init__(
        self,
        cancellation_guard: CancellationGuard,
        progress_recorder: SourceProgressRecorder,
        source_processor: SingleSourceProcessorPort,
        max_concurrency: int = MAX_CONCURRENT_SOURCES,
    ) -> None:
        self._cancellation_guard = cancellation_guard
        self._progress_recorder = progress_recorder
        self._source_processor = source_processor
        self._max_concurrency = max_concurrency

    async def process_sources(
        self, context: MonitoringExecutionContext
    ) -> MonitoringProgress:
        if getattr(context, "execution_mode", None) in ("distributed", "distributed_fanout"):
            from app.shared.domain.errors import ConflictError
            raise ConflictError(
                f"Cannot execute legacy batch processor on a run with execution_mode '{context.execution_mode}'"
            )
        total_documents = 0
        total_pages = 0
        source_count = len(context.sources)
        started_at = monotonic()
        indexed_sources = list(enumerate(context.sources, start=1))

        for batch_start in range(0, source_count, self._max_concurrency):
            if await self._cancellation_guard.is_cancelled(context.job_id):
                return MonitoringProgress(
                    documents=total_documents,
                    terminal_result={"status": "cancelled", "documents": total_documents},
                )

            batch = indexed_sources[batch_start : batch_start + self._max_concurrency]
            first_index = batch[0][0]
            last_index = batch[-1][0]

            await self._progress_recorder.record_crawl_progress(
                context.job_id,
                sources_completed=batch_start,
                source_count=source_count,
                pages_processed=total_pages,
                documents_prepared=total_documents,
                eta_seconds=None,
                message=f"در حال خزش منابع {first_index} تا {last_index} از {source_count}",
            )

            results = await asyncio.gather(
                *(
                    self._source_processor.process_source(context, source_data)
                    for _, source_data in batch
                )
            )

            if any(result is None for result in results):
                is_cancelled = await self._cancellation_guard.is_cancelled(context.job_id)
                terminal_status = "cancelled" if is_cancelled else "missing"
                return MonitoringProgress(
                    documents=total_documents,
                    terminal_result={"status": terminal_status, "documents": total_documents},
                )

            completed_results = [
                res if isinstance(res, SourceProcessingResult) else SourceProcessingResult(pages=0, documents=res)
                for res in results
                if res is not None
            ]
            total_pages += sum(res.pages for res in completed_results)
            total_documents += sum(res.documents for res in completed_results)

            sources_completed = last_index
            elapsed_seconds = max(0.001, monotonic() - started_at)
            eta_seconds = round(
                elapsed_seconds / sources_completed * (source_count - sources_completed)
            )

            await self._progress_recorder.record_crawl_progress(
                context.job_id,
                sources_completed=sources_completed,
                source_count=source_count,
                pages_processed=total_pages,
                documents_prepared=total_documents,
                eta_seconds=eta_seconds,
                message=(
                    f"خزش {sources_completed} از {source_count} منبع تکمیل شد؛ "
                    f"{total_pages} صفحه پردازش شد"
                ),
            )

        return MonitoringProgress(documents=total_documents)
