import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

import httpx
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.logging import log_event
from app.db.session import get_sessionmaker
from app.ingestion.publisher import IngestionPublishError, publish_ingestion_job
from app.ingestion.state import (
    ACTIVE_JOB_STATUSES,
    RETRYABLE_JOB_STATUSES,
    JobStatus,
    transition_allowed,
)
from app.models.ingestion_job import IngestionJob
from app.models.upload import Upload
from app.observability.metrics import (
    INGESTION_DIFY_DURATION,
    INGESTION_ENQUEUE_FAILURES,
    INGESTION_RETRY_ATTEMPTS,
    INGESTION_TRANSITIONS,
    INGESTION_WORKER_FAILURES,
    bounded_exception_type,
)
from app.schemas.upload import IngestionJobResponse
from app.services.dify_service import DifyService

logger = logging.getLogger(__name__)

SAFE_ERROR_MESSAGES = {
    "dify_timeout": "Indexing provider timed out.",
    "dify_unavailable": "Indexing provider is temporarily unavailable.",
    "dify_rate_limited": "Indexing provider is temporarily rate limited.",
    "dify_rejected": "Indexing provider rejected the document.",
    "dify_indexing_failed": "Indexing provider could not index the document.",
    "dify_indexing_paused": "Indexing provider paused the document.",
    "provider_auth": "Indexing provider configuration was rejected.",
    "file_missing": "The stored file is unavailable.",
    "unsafe_path": "The stored file reference is invalid.",
    "invalid_job": "The ingestion job is invalid.",
    "worker_internal": "The ingestion worker could not complete the job.",
    "enqueue_failed": "The ingestion request is saved but could not be queued.",
}


class IngestionJobNotFound(Exception):
    pass


class InvalidIngestionTransition(Exception):
    pass


class InvalidProviderResponse(Exception):
    pass


@dataclass(frozen=True)
class ClaimedJob:
    job_id: UUID
    upload_id: UUID
    storage_path: str
    original_filename: str
    mime_type: str
    attempt_count: int
    max_attempts: int


def _public_message(status: str) -> str:
    messages = {
        JobStatus.QUEUED: "File is queued for indexing.",
        JobStatus.PROCESSING: "File indexing is in progress.",
        JobStatus.DOCUMENT_CREATED: "Document was created; indexing is pending.",
        JobStatus.INDEXING: "Document indexing is in progress.",
        JobStatus.INDEXED: "File indexing completed.",
        JobStatus.RETRYING: "Indexing will retry after a temporary failure.",
        JobStatus.FAILED: "File indexing failed.",
        JobStatus.ENQUEUE_FAILED: "File is saved; queue publication can be retried.",
    }
    try:
        return messages[JobStatus(status)]
    except ValueError:
        return "Ingestion status is unavailable."


def to_response(job: IngestionJob) -> IngestionJobResponse:
    return IngestionJobResponse(
        job_id=job.id,
        upload_id=job.upload_id,
        status=job.status,
        attempt_count=job.attempt_count,
        queued_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failed_at=job.failed_at,
        error_code=job.error_code,
        message=_public_message(job.status),
    )


async def latest_job_for_upload(
    session: AsyncSession,
    upload_id: UUID,
    *,
    for_update: bool = False,
) -> IngestionJob | None:
    statement = (
        select(IngestionJob)
        .where(IngestionJob.upload_id == upload_id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return (await session.execute(statement)).scalar_one_or_none()


async def get_owned_job(
    session: AsyncSession,
    upload_id: UUID,
    actor_user_id: UUID,
    *,
    allow_all: bool = False,
    for_update: bool = False,
) -> IngestionJob:
    conditions = [
        IngestionJob.upload_id == upload_id,
        Upload.id == IngestionJob.upload_id,
        Upload.deleted_at.is_(None),
    ]
    if not allow_all:
        conditions.append(Upload.uploaded_by == actor_user_id)
    statement = (
        select(IngestionJob)
        .join(Upload, Upload.id == IngestionJob.upload_id)
        .where(*conditions)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update(of=IngestionJob)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        raise IngestionJobNotFound
    return job


async def get_job_status(
    session: AsyncSession,
    upload_id: UUID,
    actor_user_id: UUID,
    *,
    allow_all: bool = False,
) -> IngestionJobResponse:
    job = await get_owned_job(session, upload_id, actor_user_id, allow_all=allow_all)
    job = await refresh_dify_job_status(session, job)
    return to_response(job)


async def mark_enqueue_failed(session: AsyncSession, job_id: UUID) -> None:
    job = (
        await session.execute(
            select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
        )
    ).scalar_one_or_none()
    if job is None or not transition_allowed(job.status, JobStatus.ENQUEUE_FAILED):
        log_event(
            logger,
            logging.WARNING,
            "ingestion_transition_rejected",
            outcome="invalid_transition",
            error_type="enqueue_failed",
        )
        await session.rollback()
        return
    upload = await session.get(Upload, job.upload_id, with_for_update=True)
    job.status = JobStatus.ENQUEUE_FAILED
    job.error_code = "enqueue_failed"
    job.error_message = SAFE_ERROR_MESSAGES["enqueue_failed"]
    if upload is not None:
        upload.status = JobStatus.ENQUEUE_FAILED
        upload.error_message = SAFE_ERROR_MESSAGES["enqueue_failed"]
    await session.commit()
    INGESTION_ENQUEUE_FAILURES.inc()
    INGESTION_TRANSITIONS.labels(JobStatus.ENQUEUE_FAILED).inc()


async def retry_owned_job(
    session: AsyncSession,
    upload_id: UUID,
    actor_user_id: UUID,
    request_id: str | None,
    *,
    allow_all: bool = False,
) -> IngestionJobResponse:
    job = await get_owned_job(
        session,
        upload_id,
        actor_user_id,
        allow_all=allow_all,
        for_update=True,
    )
    try:
        current = JobStatus(job.status)
    except ValueError as exc:
        raise InvalidIngestionTransition from exc
    if current not in RETRYABLE_JOB_STATUSES or not transition_allowed(
        current,
        JobStatus.QUEUED,
        explicit_retry=True,
    ):
        raise InvalidIngestionTransition
    upload = await session.get(Upload, job.upload_id, with_for_update=True)
    if upload is None or upload.deleted_at is not None:
        raise IngestionJobNotFound
    job.status = JobStatus.QUEUED
    job.attempt_count = 0
    job.started_at = None
    job.error_code = None
    job.error_message = None
    job.failed_at = None
    job.completed_at = None
    upload.status = JobStatus.QUEUED
    upload.error_message = None
    await session.commit()
    INGESTION_RETRY_ATTEMPTS.labels("explicit").inc()
    INGESTION_TRANSITIONS.labels(JobStatus.QUEUED).inc()
    try:
        await publish_ingestion_job(job.id, request_id)
    except IngestionPublishError:
        await mark_enqueue_failed(session, job.id)
    refreshed = await get_owned_job(session, upload_id, actor_user_id, allow_all=allow_all)
    log_event(logger, logging.INFO, "ingestion_retry_requested", outcome=refreshed.status)
    return to_response(refreshed)


def _safe_storage_file(storage_path: str) -> Path:
    root = settings.upload_storage_path.resolve(strict=True)
    candidate = Path(storage_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if not candidate.exists():
            raise FileNotFoundError from exc
        raise PermissionError from exc
    if resolved != candidate.absolute() or not resolved.is_file() or resolved.is_symlink():
        raise PermissionError
    return resolved


async def _claim_job(job_id: UUID, *, redelivered: bool) -> ClaimedJob | None:
    async with get_sessionmaker()() as session:
        job = (
            await session.execute(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .options(selectinload(IngestionJob.upload))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.upload is None or job.upload.deleted_at is not None:
            return None
        allowed = transition_allowed(job.status, JobStatus.PROCESSING)
        allowed = allowed or (redelivered and job.status == JobStatus.PROCESSING)
        if not allowed or job.attempt_count >= job.max_attempts:
            log_event(
                logger,
                logging.WARNING,
                "ingestion_claim_rejected",
                outcome="invalid_state",
            )
            return None
        job.status = JobStatus.PROCESSING
        job.attempt_count += 1
        job.started_at = datetime.now(UTC)
        job.error_code = None
        job.error_message = None
        job.upload.status = JobStatus.PROCESSING
        job.upload.error_message = None
        await session.commit()
        INGESTION_TRANSITIONS.labels(JobStatus.PROCESSING).inc()
        return ClaimedJob(
            job_id=job.id,
            upload_id=job.upload_id,
            storage_path=job.upload.storage_path,
            original_filename=job.upload.original_filename,
            mime_type=job.upload.mime_type,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
        )


async def _finish_document_created(job_id: UUID, document_id: UUID) -> None:
    async with get_sessionmaker()() as session:
        job = (
            await session.execute(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .options(selectinload(IngestionJob.upload))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.upload is None or not transition_allowed(
            job.status, JobStatus.DOCUMENT_CREATED
        ):
            raise InvalidIngestionTransition
        job.status = JobStatus.DOCUMENT_CREATED
        job.dify_document_id = document_id
        job.completed_at = None
        job.failed_at = None
        job.error_code = None
        job.error_message = None
        job.upload.status = JobStatus.DOCUMENT_CREATED
        job.upload.dify_document_id = document_id
        job.upload.error_message = None
        await session.commit()
        INGESTION_TRANSITIONS.labels(JobStatus.DOCUMENT_CREATED).inc()


async def refresh_dify_job_status(
    session: AsyncSession,
    job: IngestionJob,
) -> IngestionJob:
    if job.status not in {JobStatus.DOCUMENT_CREATED, JobStatus.INDEXING}:
        return job
    if job.dify_document_id is None:
        return job
    try:
        document = await DifyService().get_document(str(job.dify_document_id))
    except httpx.HTTPError as exc:
        log_event(
            logger,
            logging.WARNING,
            "dify_status_refresh_failed",
            outcome="temporary_failure",
            error_type=bounded_exception_type(exc),
            dependency="dify",
        )
        return job
    if document is None:
        return job

    indexing_status = str(document.get("indexing_status") or "").lower()
    fresh_job = await session.get(IngestionJob, job.id, with_for_update=True)
    if fresh_job is None:
        raise IngestionJobNotFound
    upload = await session.get(Upload, fresh_job.upload_id, with_for_update=True)
    if upload is None:
        raise IngestionJobNotFound

    if indexing_status == "completed":
        now = datetime.now(UTC)
        fresh_job.status = JobStatus.INDEXED
        fresh_job.completed_at = now
        fresh_job.failed_at = None
        fresh_job.error_code = None
        fresh_job.error_message = None
        upload.status = JobStatus.INDEXED
        upload.error_message = None
        INGESTION_TRANSITIONS.labels(JobStatus.INDEXED).inc()
    elif indexing_status in {"error", "paused"}:
        now = datetime.now(UTC)
        fresh_job.status = JobStatus.FAILED
        fresh_job.failed_at = now
        error_code = (
            "dify_indexing_paused"
            if indexing_status == "paused"
            else "dify_indexing_failed"
        )
        fresh_job.error_code = error_code
        fresh_job.error_message = SAFE_ERROR_MESSAGES[error_code]
        upload.status = JobStatus.FAILED
        upload.error_message = SAFE_ERROR_MESSAGES[error_code]
        INGESTION_TRANSITIONS.labels(JobStatus.FAILED).inc()
    elif fresh_job.status == JobStatus.DOCUMENT_CREATED:
        fresh_job.status = JobStatus.INDEXING
        upload.status = JobStatus.INDEXING
        INGESTION_TRANSITIONS.labels(JobStatus.INDEXING).inc()

    await session.commit()
    return fresh_job


async def _finish_failure(
    job_id: UUID,
    error_code: str,
    *,
    transient: bool,
) -> str:
    async with get_sessionmaker()() as session:
        job = (
            await session.execute(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .options(selectinload(IngestionJob.upload))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if job is None or job.upload is None or not transition_allowed(
            job.status, JobStatus.RETRYING if transient else JobStatus.FAILED
        ):
            return JobStatus.FAILED
        will_retry = transient and job.attempt_count < job.max_attempts
        target = JobStatus.RETRYING if will_retry else JobStatus.FAILED
        job.status = target
        job.error_code = error_code
        job.error_message = SAFE_ERROR_MESSAGES[error_code]
        job.upload.status = target
        job.upload.error_message = SAFE_ERROR_MESSAGES[error_code]
        if target is JobStatus.FAILED:
            job.failed_at = datetime.now(UTC)
        await session.commit()
        INGESTION_TRANSITIONS.labels(target).inc()
        INGESTION_WORKER_FAILURES.labels(error_code).inc()
        if will_retry:
            INGESTION_RETRY_ATTEMPTS.labels("automatic").inc()
        return target


def _classify_error(exc: Exception) -> tuple[str, bool]:
    if isinstance(exc, (httpx.TimeoutException, SoftTimeLimitExceeded)):
        return "dify_timeout", True
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "dify_rate_limited", True
        if status in {502, 503, 504}:
            return "dify_unavailable", True
        if status in {401, 403}:
            return "provider_auth", False
        return "dify_rejected", False
    if isinstance(exc, httpx.TransportError):
        return "dify_unavailable", True
    if isinstance(exc, InvalidProviderResponse):
        return "dify_rejected", False
    if isinstance(exc, FileNotFoundError):
        return "file_missing", False
    if isinstance(exc, PermissionError):
        return "unsafe_path", False
    return "worker_internal", True


async def process_ingestion_job(job_id: UUID, *, redelivered: bool) -> str:
    claimed = await _claim_job(job_id, redelivered=redelivered)
    if claimed is None:
        return "ignored"
    try:
        path = _safe_storage_file(claimed.storage_path)
        started = perf_counter()
        try:
            result = await DifyService().create_document_by_file(
                file_path=path,
                filename=claimed.original_filename,
                mime_type=claimed.mime_type,
            )
        finally:
            INGESTION_DIFY_DURATION.observe(perf_counter() - started)
        document = result.get("document")
        document_id = None
        if isinstance(document, dict) and document.get("id"):
            document_id = UUID(str(document["id"]))
        if document_id is None:
            raise InvalidProviderResponse
        await _finish_document_created(job_id, document_id)
        return JobStatus.DOCUMENT_CREATED
    except Exception as exc:
        error_code, transient = _classify_error(exc)
        log_event(
            logger,
            logging.WARNING if transient else logging.ERROR,
            "ingestion_task_failed",
            outcome="retrying" if transient else "failed",
            error_type=error_code,
            dependency="dify" if error_code.startswith("dify_") else None,
        )
        return await _finish_failure(job_id, error_code, transient=transient)


def is_upload_busy(job: IngestionJob | None) -> bool:
    if job is None:
        return False
    try:
        return JobStatus(job.status) in ACTIVE_JOB_STATUSES
    except ValueError:
        return False
