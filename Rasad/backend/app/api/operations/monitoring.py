"""Monitoring transport mapping around focused application use cases."""

from dataclasses import dataclass
from math import ceil
from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.identity.domain.roles import UserRole
from app.modules.monitoring.api import (
    CancelMonitoringJob,
    CreateMonitoringJob,
    DeleteMonitoringJob,
    GetMonitoringJob,
    ListMonitoringJobs,
    MonitoringJobDraft,
    MonitoringJobQueue,
    MonitoringJobRecord,
    RetryMonitoringJob,
    UpdateMonitoringJob,
)
from app.modules.monitoring.domain.progress import decode_progress
from app.schemas.common import PageMeta, PaginatedResponse
from app.schemas.monitoring import (
    MonitoringJobCreate,
    MonitoringJobProgress,
    MonitoringJobRead,
    MonitoringJobUpdate,
)
from app.shared.domain.errors import NotFoundError, ValidationError


@dataclass(frozen=True)
class MonitoringQueries:
    list_jobs: ListMonitoringJobs
    get_job: GetMonitoringJob


@dataclass(frozen=True)
class MonitoringCommands:
    create_job: CreateMonitoringJob
    update_job: UpdateMonitoringJob
    retry_job: RetryMonitoringJob
    cancel_job: CancelMonitoringJob
    delete_job: DeleteMonitoringJob


def _draft(payload: MonitoringJobCreate, requester_id: UUID) -> MonitoringJobDraft:
    if payload.date_from_gregorian is None or payload.date_to_gregorian is None:
        raise ValidationError("Monitoring date range was not normalized")
    return MonitoringJobDraft(
        topic=payload.topic,
        description=payload.description,
        date_from_jalali=payload.date_from_jalali,
        date_to_jalali=payload.date_to_jalali,
        date_from_gregorian=payload.date_from_gregorian,
        date_to_gregorian=payload.date_to_gregorian,
        source_ids=payload.source_ids,
        bot_identity_id=payload.bot_identity_id,
        requester_id=requester_id,
    )


def _to_read(job: MonitoringJobRecord, durable_progress=None) -> MonitoringJobRead:
    if job.bot_identity_id is None:
        raise ValidationError("Monitoring job has no bot identity")
    snapshot = decode_progress(job.error_message)
    effective_status = job.status.value
    if (durable_progress is not None and durable_progress.stage == "cancelled"
        and effective_status in {"pending", "crawling", "processing", "analyzing"}):
        effective_status = "cancelled"
    progress = None
    if durable_progress is not None:
        progress = MonitoringJobProgress(**durable_progress.__dict__)
    elif snapshot is not None:
        progress = MonitoringJobProgress(**snapshot.__dict__)
    elif job.status.value in {"pending", "crawling", "processing", "analyzing"}:
        fallback_percent = {
            "pending": 0,
            "crawling": 5,
            "processing": 75,
            "analyzing": 85,
        }[job.status.value]
        progress = MonitoringJobProgress(
            stage=job.status.value,
            percent=fallback_percent,
            sources_completed=0,
            sources_total=len(job.sources),
            pages_processed=0,
            documents_prepared=0,
            eta_seconds=None,
            message="در حال آماده‌سازی اطلاعات پیشرفت",
        )
    return MonitoringJobRead(
        id=job.id,
        topic=job.topic,
        description=job.description,
        status=effective_status,
        date_from_jalali=job.date_from_jalali,
        date_to_jalali=job.date_to_jalali,
        source_ids=[source.id for source in job.sources],
        bot_identity_id=job.bot_identity_id,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=None if snapshot is not None else job.error_message,
        progress=progress,
    )


class MonitoringOperations:
    def __init__(
        self,
        queries: MonitoringQueries,
        commands: MonitoringCommands,
        queue: MonitoringJobQueue,
        transaction: TransactionRunner,
        progress_reader=None,
    ) -> None:
        self._queries = queries
        self._commands = commands
        self._queue = queue
        self._transaction = transaction
        self._progress_reader = progress_reader

    async def create(self, payload: MonitoringJobCreate, requester_id: UUID) -> MonitoringJobRead:
        try:
            job = await self._transaction.run(
                lambda: self._commands.create_job.execute(_draft(payload, requester_id))
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        self._queue.enqueue(job.id)
        return _to_read(job)

    async def list(
        self, page: int, page_size: int, requester_id: UUID, role: UserRole
    ) -> PaginatedResponse[MonitoringJobRead]:
        jobs, total = await self._queries.list_jobs.execute(
            page=page, page_size=page_size, requester_id=requester_id, role=role
        )
        progress = await self._progress_reader.read_many([j.id for j in jobs]) if self._progress_reader else {}
        return PaginatedResponse(
            items=[_to_read(job, progress.get(job.id)) for job in jobs],
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def get(self, job_id: UUID, requester_id: UUID, role: UserRole) -> MonitoringJobRead:
        job = await self._queries.get_job.execute(job_id, requester_id=requester_id, role=role)
        if job is None:
            raise NotFoundError("Monitoring job not found")
        progress = await self._progress_reader.read_many([job.id]) if self._progress_reader else {}
        return _to_read(job, progress.get(job.id))

    async def update(
        self, job_id: UUID, payload: MonitoringJobUpdate, requester_id: UUID, role: UserRole
    ) -> MonitoringJobRead:
        try:
            job = await self._transaction.run(
                lambda: self._commands.update_job.execute(
                    job_id,
                    _draft(payload, requester_id),
                    requester_id=requester_id,
                    role=role,
                )
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if job is None:
            raise NotFoundError("Monitoring job not found")
        self._queue.enqueue(job.id)
        return _to_read(job)

    async def retry(self, job_id: UUID, requester_id: UUID, role: UserRole) -> None:
        try:
            retried = await self._transaction.run(
                lambda: self._commands.retry_job.execute(
                    job_id, requester_id=requester_id, role=role
                )
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        if not retried:
            raise NotFoundError("Monitoring job not found")
        self._queue.enqueue(job_id)

    async def cancel(self, job_id: UUID, requester_id: UUID, role: UserRole) -> None:
        cancelled = await self._transaction.run(
            lambda: self._commands.cancel_job.execute(job_id, requester_id=requester_id, role=role)
        )
        if not cancelled:
            raise NotFoundError("Monitoring job not found")

    async def delete(self, job_id: UUID, requester_id: UUID, role: UserRole) -> None:
        deleted = await self._transaction.run(
            lambda: self._commands.delete_job.execute(job_id, requester_id=requester_id, role=role)
        )
        if not deleted:
            raise NotFoundError("Monitoring job not found")
