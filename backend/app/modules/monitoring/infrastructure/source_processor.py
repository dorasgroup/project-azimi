"""Individual source crawl, preparation, and diagnostic execution."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crawler.engine import CrawlEngine, crawl_execution_trace
from app.models import MonitoringJob, Source
from app.models.enums import JobStatus, LogLevel
from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.documents.ports import DocumentRepository
from app.modules.monitoring.application.source_failure import SourceFailureRecorder
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
    SourceProcessingResult,
)
from app.modules.monitoring.domain import SourceExecutionDiagnoser, SourceExecutionFacts
from app.modules.monitoring.domain.crawl_policy import effective_crawl_policy
from app.modules.monitoring.domain.execution import SOURCE_TASK_LEASE_SECONDS, SourceTaskStatus
from app.modules.monitoring.infrastructure.crawl_health import SourceCrawlHealthUpdater
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemyMonitoringRunRepository,
    SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.infrastructure.log_operations import record_system_log
from app.modules.monitoring.ports import MonitoringJobRepository
from app.processing.pipeline import ContentProcessor
from app.processing.schemas import PreparedDocument, ProcessingPolicy

logger = logging.getLogger("rasad.worker.monitoring")
SOURCE_CRAWL_TIMEOUT_SECONDS = 180


class SingleSourceProcessorPort(Protocol):
    async def process_source(
        self,
        context: MonitoringExecutionContext,
        source_data: MonitoringSourceSnapshot,
    ) -> SourceProcessingResult | None: ...


class SingleSourceProcessor:
    """Execute guarded crawl, content preparation, and diagnostics for one source."""

    def __init__(
        self,
        *args,
        session_factory: Callable | None = None,
        monitoring_repositories: Callable | None = None,
        document_repositories: Callable | None = None,
        map_document: Callable | None = None,
        failure_recorder: SourceFailureRecorder | None = None,
        crawler: CrawlEngine | None = None,
        content_processor: Callable | None = None,
    ) -> None:
        if len(args) == 5:
            self._session_factory = args[0]
            self._monitoring_repositories = args[1]
            self._document_repositories = args[2]
            self._map_document = args[3]
            self._failure_recorder = args[4]
            self._crawler = None
            self._content_processor = None
        elif len(args) == 4:
            self._crawler = args[0]
            self._content_processor = args[1]
            self._failure_recorder = args[2]
            self._session_factory = args[3]
            self._monitoring_repositories = None
            self._document_repositories = None
            self._map_document = None
        else:
            self._session_factory = session_factory
            self._monitoring_repositories = monitoring_repositories
            self._document_repositories = document_repositories
            self._map_document = map_document
            self._failure_recorder = failure_recorder
            self._crawler = crawler
            self._content_processor = content_processor

    async def process_source(
        self,
        context: MonitoringExecutionContext,
        source_data: MonitoringSourceSnapshot,
    ) -> SourceProcessingResult | None:
        task_id: UUID | None = None
        claim_token: UUID | None = None
        run_id = getattr(context, "run_id", None)
        if run_id is not None and self._session_factory is not None:
            async with self._session_factory() as session:
                task_repo = SqlAlchemySourceTaskRepository(session)
                task = await task_repo.get_by_run_and_source(run_id, source_data["id"])
                if task is not None:
                    task_id = task.id
                    claimed, token = await task_repo.claim_task(
                        task_id, lease_duration_seconds=SOURCE_TASK_LEASE_SECONDS
                    )
                    if claimed:
                        claim_token = token
                        await session.commit()
                    else:
                        return None

        try:
            async with asyncio.timeout(SOURCE_CRAWL_TIMEOUT_SECONDS):
                result = await self._execute_source_crawl(context, source_data)

            if result is not None and run_id is not None and task_id is not None and claim_token is not None and self._session_factory is not None:
                async with self._session_factory() as session:
                    task_repo = SqlAlchemySourceTaskRepository(session)
                    await task_repo.complete_task_and_update_run(
                        task_id,
                        documents_count=result.documents,
                        claim_token=claim_token,
                    )
                    await session.commit()

            return result
        except Exception as exc:
            logger.exception(
                "Source crawl failed; monitoring will continue",
                extra={
                    "job_id": str(context.job_id),
                    "source_id": str(source_data["id"]),
                    "source_title": source_data["title"],
                },
            )
            if self._failure_recorder is not None:
                await self._failure_recorder.record_failure(
                    context.job_id, source_data["id"], source_data["title"], exc
                )

            if run_id is not None and task_id is not None and claim_token is not None and self._session_factory is not None:
                async with self._session_factory() as session:
                    task_repo = SqlAlchemySourceTaskRepository(session)
                    err_code = "TIMEOUT" if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) else "CRAWL_FAILED"
                    err_msg = str(exc)[:250] or "Source crawl failed"
                    await task_repo.fail_task_and_update_run(
                        task_id,
                        error_code=err_code,
                        error_message=err_msg,
                        claim_token=claim_token,
                    )
                    await session.commit()

            return SourceProcessingResult(pages=0, documents=0)

    async def _execute_source_crawl(
        self,
        context: MonitoringExecutionContext,
        source_data: MonitoringSourceSnapshot,
    ) -> SourceProcessingResult | None:
        if self._crawler is not None and self._monitoring_repositories is None:
            trace_token = crawl_execution_trace.set({
                "job_id": str(context.job_id),
                "source_id": str(source_data["id"]),
                "source_title": source_data["title"],
            })
            try:
                return await self._crawler.crawl_source(
                    source_id=source_data["id"],
                    topic=context.topic,
                    date_from=context.date_from,
                    date_to=context.date_to,
                    crawl_config=source_data.get("crawl_config") or {},
                )
            finally:
                crawl_execution_trace.reset(trace_token)

        source_config = source_data.get("crawl_config") or {}
        crawl_policy = effective_crawl_policy(
            source_config,
            date_from=context.date_from,
            date_to=context.date_to,
            source_count=len(context.sources),
        )
        processing_policy = ProcessingPolicy.model_validate(
            source_config.get("processing_policy", {})
        )
        search_topic = (
            f"{context.topic} {context.description}" if context.description else context.topic
        )
        crawl_engine = CrawlEngine(crawl_policy)
        crawl_result = await crawl_engine.crawl(
            source_data["base_url"], search_topic, context.date_from, context.date_to
        )
        async with self._session_factory() as session:
            repository = self._monitoring_repositories(session)
            job = await repository.get_by_id(context.job_id, include_sources=True)
            if job is None or job.status == JobStatus.CANCELLED:
                return None
            source = await session.get(Source, source_data["id"])
            if source is None:
                return SourceProcessingResult(pages=0, documents=0)

            SourceCrawlHealthUpdater.update(source, crawl_result)
            prepared = ContentProcessor(processing_policy).prepare_batch(
                crawl_result.pages,
                search_topic,
                source.title,
                context.date_from,
                context.date_to,
            )
            inserted = await self._document_repositories(session).replace_for_source(
                job.id,
                source.id,
                [self._map_document(document) for document in prepared.documents],
            )
            rejection_reasons = Counter(
                item.reason.splitlines()[0][:240] for item in prepared.rejected
            )
            source_diagnostics = SourceExecutionDiagnoser.diagnose(
                SourceExecutionFacts(
                    crawl_status=crawl_result.diagnostics.status,
                    crawl_reason_code=crawl_result.diagnostics.reason_code,
                    crawl_summary=crawl_result.diagnostics.summary,
                    discovered_total=crawl_result.diagnostics.discovered_total,
                    selected_total=crawl_result.diagnostics.selected_total,
                    fetched_total=crawl_result.diagnostics.fetched_total,
                    successful_pages=crawl_result.diagnostics.successful_pages,
                    provider_counts=crawl_result.diagnostics.provider_counts,
                    selected_provider_counts=(
                        crawl_result.diagnostics.selected_provider_counts
                    ),
                    prepared_documents=len(prepared.documents),
                    rejection_reasons=tuple(item.reason for item in prepared.rejected),
                    duplicate_count=prepared.duplicate_count,
                )
            )
            await record_system_log(
                session,
                level=LogLevel.INFO,
                module="monitoring_worker",
                message="Source crawled and prepared",
                job_id=job.id,
                context={
                    "source_id": str(source.id),
                    "source_title": source.title,
                    "discovered_links": len(crawl_result.discovered_links),
                    "crawled_pages": len(crawl_result.pages),
                    "crawl_max_pages": crawl_policy.max_pages,
                    "crawl_diagnostics": crawl_result.diagnostics.model_dump(mode="json"),
                    "crawl_stage_trace": crawl_execution_trace(crawl_engine),
                    "source_diagnostics": source_diagnostics.as_dict(),
                    "prepared_documents": len(prepared.documents),
                    "rejected_documents": len(prepared.rejected),
                    "rejection_reasons": dict(rejection_reasons.most_common(5)),
                },
            )
            await session.commit()
            return SourceProcessingResult(pages=len(crawl_result.pages), documents=inserted)
