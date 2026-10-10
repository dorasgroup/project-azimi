"""Fenced transactional persistence for source execution results and document snapshots."""

from __future__ import annotations

from collections import Counter
import logging
from typing import Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.enums import JobStatus, LogLevel
from app.modules.monitoring.domain.execution import SourceTaskStatus
from app.models.source import Source
from app.models.source_task import SourceTask as OrmSourceTask
from app.models.source_task_document import SourceTaskDocument
from app.modules.documents.domain import PreparedDocumentRecord
from app.modules.documents.ports import DocumentRepository
from app.modules.monitoring.application.source_failure import SourceFailureRecorder
from app.modules.monitoring.domain import (
    SourceExecutionDiagnoser,
    SourceExecutionFacts,
)
from app.modules.monitoring.infrastructure.crawl_health import SourceCrawlHealthUpdater
from app.modules.monitoring.infrastructure.execution_repository import (
    SqlAlchemySourceTaskRepository,
)
from app.modules.monitoring.infrastructure.log_operations import record_system_log
from app.modules.monitoring.ports import MonitoringJobRepository
from app.processing.schemas import PreparedDocument
from app.shared.integration.outbox import enqueue
from app.shared.infrastructure.run_fence import lock_parent_for_child

logger = logging.getLogger("rasad.worker.persister")


class SourceResultPersister:
    """Fenced transactional persistence for source execution results."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession],
        monitoring_repositories: Callable[[AsyncSession], MonitoringJobRepository],
        document_repositories: Callable[[AsyncSession], DocumentRepository],
        map_document: Callable[[PreparedDocument], PreparedDocumentRecord],
        failure_recorder: SourceFailureRecorder,
    ) -> None:
        self._session_factory = session_factory
        self._monitoring_repositories = monitoring_repositories
        self._document_repositories = document_repositories
        self._map_document = map_document
        self._failure_recorder = failure_recorder

    async def persist_success(
        self,
        task_id: UUID,
        claim_token: UUID,
        job_id: UUID,
        source_id: UUID,
        crawl_result: object,
        prepared_batch: object,
        crawl_policy: object,
    ) -> bool:
        """Atomically persist documents and terminalize task only if claim token remains valid."""
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmSourceTask.run_id, OrmSourceTask.id, task_id
            ) in (None, "cancelled"):
                return False
            # Side-effect fencing (TOCTOU guard) via row-level locking
            stmt = (
                select(OrmSourceTask)
                .where(
                    OrmSourceTask.id == task_id,
                    OrmSourceTask.status == SourceTaskStatus.RUNNING,
                    OrmSourceTask.claim_token == claim_token,
                )
                .with_for_update()
            )
            task_orm = (await session.execute(stmt)).scalar_one_or_none()
            if not task_orm:
                logger.warning(
                    "Stale worker rejected by fence for task %s (token %s). Discarding results.",
                    task_id,
                    claim_token,
                )
                return False

            repository = self._monitoring_repositories(session)
            job = await repository.get_by_id(job_id, include_sources=True)
            if job is None or job.status == JobStatus.CANCELLED:
                logger.info("Job %s cancelled or missing; discarding task %s results", job_id, task_id)
                return False

            source = await session.get(Source, source_id)
            if source is None:
                logger.warning("Source %s not found during persistence of task %s", source_id, task_id)
                return False

            # Update health and documents
            if hasattr(crawl_result, "probe") and crawl_result.probe is not None:
                SourceCrawlHealthUpdater.update(source, crawl_result)
            doc_repo = self._document_repositories(session)
            mapped_docs = [self._map_document(doc) for doc in getattr(prepared_batch, "documents", [])]
            inserted = await doc_repo.replace_for_source(job.id, source.id, mapped_docs)

            # Snapshot documents into source_task_documents table (Option B snapshot isolation)
            for doc in mapped_docs:
                session.add(
                    SourceTaskDocument(
                        source_task_id=task_id,
                        run_id=task_orm.run_id,
                        source_id=source_id,
                        url=str(doc.url),
                        canonical_url=doc.canonical_url,
                        title=doc.title,
                        clean_text=doc.clean_text,
                        published_at=doc.published_at,
                        language=doc.language,
                        matched_score=doc.matched_score,
                        content_hash=doc.content_hash,
                    )
                )

            # Atomic task completion and run counter increment
            task_repo = SqlAlchemySourceTaskRepository(session)
            completed = await task_repo.complete_task_and_update_run(
                task_id,
                documents_count=inserted,
                claim_token=claim_token,
            )
            if not completed:
                logger.warning("Failed to terminalize task %s in run counters", task_id)
                return False

            # Record system log
            rejection_reasons = Counter(
                item.reason.splitlines()[0][:240] for item in getattr(prepared_batch, "rejected", [])
            )
            source_diagnostics = SourceExecutionDiagnoser.diagnose(
                SourceExecutionFacts(
                    crawl_status=getattr(crawl_result, "diagnostics").status,
                    crawl_reason_code=getattr(crawl_result, "diagnostics").reason_code,
                    crawl_summary=getattr(crawl_result, "diagnostics").summary,
                    discovered_total=getattr(crawl_result, "diagnostics").discovered_total,
                    selected_total=getattr(crawl_result, "diagnostics").selected_total,
                    fetched_total=getattr(crawl_result, "diagnostics").fetched_total,
                    successful_pages=getattr(crawl_result, "diagnostics").successful_pages,
                    provider_counts=getattr(crawl_result, "diagnostics").provider_counts,
                    selected_provider_counts=getattr(crawl_result, "diagnostics").selected_provider_counts,
                    prepared_documents=len(getattr(prepared_batch, "documents", [])),
                    rejection_reasons=tuple(item.reason for item in getattr(prepared_batch, "rejected", [])),
                    duplicate_count=getattr(prepared_batch, "duplicate_count", 0),
                )
            )
            await record_system_log(
                session,
                level=LogLevel.INFO,
                module="monitoring_worker",
                message="Source crawled and prepared (distributed)",
                job_id=job.id,
                context={
                    "task_id": str(task_id),
                    "source_id": str(source.id),
                    "source_title": source.title,
                    "discovered_links": len(getattr(crawl_result, "discovered_links", [])),
                    "crawled_pages": len(getattr(crawl_result, "pages", [])),
                    "crawl_max_pages": getattr(crawl_policy, "max_pages", 0),
                    "crawl_diagnostics": getattr(crawl_result, "diagnostics").model_dump(mode="json"),
                    "source_diagnostics": source_diagnostics.as_dict(),
                    "prepared_documents": len(getattr(prepared_batch, "documents", [])),
                    "rejected_documents": len(getattr(prepared_batch, "rejected", [])),
                    "rejection_reasons": dict(rejection_reasons.most_common(5)),
                },
            )

            await enqueue(
                session,
                message_key=f"source-analysis-handoff:{task_id}",
                message_type="SOURCE_ANALYSIS_HANDOFF",
                payload={"schema_version": 1, "source_task_id": str(task_id)},
            )
            await enqueue(
                session,
                message_key=f"source-terminal:{task_id}",
                message_type="SOURCE_TERMINAL",
                payload={
                    "schema_version": 1, "event": "source_terminal",
                    "run_id": str(task_orm.run_id), "source_task_id": str(task_id),
                    "outcome": "completed",
                },
            )
            await session.commit()

        return True

    async def persist_failure(
        self,
        task_id: UUID,
        claim_token: UUID,
        job_id: UUID,
        source_id: UUID,
        error: Exception,
    ) -> bool:
        """Atomically record failure and update task/run counters if token remains valid."""
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmSourceTask.run_id, OrmSourceTask.id, task_id
            ) in (None, "cancelled"):
                return False
            stmt = (
                select(OrmSourceTask)
                .where(
                    OrmSourceTask.id == task_id,
                    OrmSourceTask.status == SourceTaskStatus.RUNNING,
                    OrmSourceTask.claim_token == claim_token,
                )
                .with_for_update()
            )
            locked = (await session.execute(stmt)).scalar_one_or_none()
            if not locked:
                logger.warning(
                    "Stale worker failure rejected by fence for task %s (token %s).",
                    task_id,
                    claim_token,
                )
                return False

            source = await session.get(Source, source_id)
            title = source.title if source else str(source_id)
            await self._failure_recorder.record_failure(job_id, source_id, title, error)

            task_repo = SqlAlchemySourceTaskRepository(session)
            err_code = "TIMEOUT" if isinstance(error, TimeoutError) else "CRAWL_FAILED"
            err_msg = str(error)[:250] or "Source crawl failed"
            failed = await task_repo.fail_task_and_update_run(
                task_id,
                error_code=err_code,
                error_message=err_msg,
                claim_token=claim_token,
            )
            if not failed:
                await session.rollback()
                return False
            await enqueue(
                session,
                message_key=f"source-terminal:{task_id}",
                message_type="SOURCE_TERMINAL",
                payload={
                    "schema_version": 1, "event": "source_terminal",
                    "run_id": str(locked.run_id), "source_task_id": str(task_id),
                    "outcome": "failed",
                },
            )
            await session.commit()
            return True
