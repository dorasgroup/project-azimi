"""Single-purpose application use cases for monitoring jobs."""

from __future__ import annotations

from uuid import UUID

from app.modules.monitoring.domain import JobLifecycle, LifecycleState, MonitoringJobStatus
from app.modules.monitoring.ports import (
    MonitoringJobDraft,
    MonitoringJobRecord,
    MonitoringJobOutputCleaner,
    MonitoringJobRepository,
    RoleValue,
    StatusValue,
)
from app.shared.domain.clock import Clock


def _is_super_admin(role: str | RoleValue) -> bool:
    return getattr(role, "value", role) == "super_admin"


def _state(job: MonitoringJobRecord) -> LifecycleState:
    return LifecycleState(
        status=MonitoringJobStatus(getattr(job.status, "value", job.status)),
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_message=job.error_message,
    )


def _apply_state(job: MonitoringJobRecord, state: LifecycleState) -> None:
    current_status = job.status
    job.status = type(current_status)(state.status.value)
    job.started_at = state.started_at
    job.finished_at = state.finished_at
    job.error_message = state.error_message


class CreateMonitoringJob:
    def __init__(self, repository: MonitoringJobRepository) -> None:
        self._repository = repository

    async def execute(self, draft: MonitoringJobDraft) -> MonitoringJobRecord:
        return await self._repository.create(draft)


class GetMonitoringJob:
    def __init__(self, repository: MonitoringJobRepository) -> None:
        self._repository = repository

    async def execute(
        self, job_id: UUID, *, requester_id: UUID | None, role: str | RoleValue
    ) -> MonitoringJobRecord | None:
        return await self._repository.get_accessible_by_id(
            job_id, requester_id=requester_id, is_super_admin=_is_super_admin(role)
        )


class ListMonitoringJobs:
    def __init__(self, repository: MonitoringJobRepository) -> None:
        self._repository = repository

    async def execute(
        self, *, page: int, page_size: int, requester_id: UUID | None, role: str | RoleValue
    ) -> tuple[list[MonitoringJobRecord], int]:
        return await self._repository.list_for_actor(
            page=page,
            page_size=page_size,
            requester_id=requester_id,
            is_super_admin=_is_super_admin(role),
        )


class UpdateMonitoringJob:
    def __init__(
        self, repository: MonitoringJobRepository, output_cleaner: MonitoringJobOutputCleaner
    ) -> None:
        self._repository = repository
        self._output_cleaner = output_cleaner

    async def execute(
        self,
        job_id: UUID,
        draft: MonitoringJobDraft,
        *,
        requester_id: UUID | None,
        role: str | RoleValue,
    ) -> MonitoringJobRecord | None:
        job = await self._repository.get_accessible_by_id(
            job_id,
            requester_id=requester_id,
            is_super_admin=_is_super_admin(role),
            include_report=True,
        )
        if job is None:
            return None
        new_state = JobLifecycle.reset_for_edit(_state(job))
        await self._output_cleaner.clear(job)
        sources = await self._repository.active_sources(draft.source_ids)
        identity_id = draft.bot_identity_id or await self._repository.default_identity_id()
        job.topic = draft.topic
        job.description = draft.description
        job.date_from_jalali = draft.date_from_jalali
        job.date_to_jalali = draft.date_to_jalali
        job.date_from_gregorian = draft.date_from_gregorian
        job.date_to_gregorian = draft.date_to_gregorian
        job.bot_identity_id = identity_id
        job.sources = sources
        _apply_state(job, new_state)
        await self._repository.save()
        return job


class RetryMonitoringJob:
    def __init__(
        self, repository: MonitoringJobRepository, output_cleaner: MonitoringJobOutputCleaner
    ) -> None:
        self._repository = repository
        self._output_cleaner = output_cleaner

    async def execute(
        self, job_id: UUID, *, requester_id: UUID | None, role: str | RoleValue
    ) -> bool:
        job = await self._repository.get_accessible_by_id(
            job_id,
            requester_id=requester_id,
            is_super_admin=_is_super_admin(role),
            include_report=True,
        )
        if job is None:
            return False
        new_state = JobLifecycle.reset_for_retry(_state(job))
        await self._output_cleaner.clear(job)
        _apply_state(job, new_state)
        await self._repository.save()
        return True


class CancelMonitoringJob:
    def __init__(self, repository: MonitoringJobRepository, clock: Clock, run_closure=None) -> None:
        self._repository = repository
        self._clock = clock
        self._run_closure = run_closure

    async def execute(
        self, job_id: UUID, *, requester_id: UUID | None, role: str | RoleValue
    ) -> bool:
        job = await self._repository.get_accessible_by_id(
            job_id, requester_id=requester_id, is_super_admin=_is_super_admin(role)
        )
        if job is None:
            return False
        if self._run_closure is not None:
            await self._run_closure.close_active_for_job(job_id)
        _apply_state(job, JobLifecycle.cancel(_state(job), now=self._clock.now()))
        await self._repository.save()
        return True


class DeleteMonitoringJob:
    def __init__(
        self, repository: MonitoringJobRepository, output_cleaner: MonitoringJobOutputCleaner
    ) -> None:
        self._repository = repository
        self._output_cleaner = output_cleaner

    async def execute(
        self, job_id: UUID, *, requester_id: UUID | None, role: str | RoleValue
    ) -> bool:
        job = await self._repository.get_accessible_by_id(
            job_id,
            requester_id=requester_id,
            is_super_admin=_is_super_admin(role),
            include_report=True,
        )
        if job is None:
            return False
        await self._output_cleaner.clear_files(job)
        await self._repository.delete(job)
        await self._repository.save()
        return True


class MarkMonitoringJobStatus:
    def __init__(self, repository: MonitoringJobRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    async def execute(
        self,
        job: MonitoringJobRecord,
        status: str | StatusValue,
        *,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        target = MonitoringJobStatus(getattr(status, "value", status))
        _apply_state(
            job,
            JobLifecycle.transition(
                _state(job),
                target,
                now=self._clock.now(),
                error_message=error_message,
                finished=finished,
            ),
        )
        await self._repository.save()


class StartMonitoringJob:
    """Explicit use case for moving a queued job into the crawling state."""

    def __init__(self, status_updater: MarkMonitoringJobStatus) -> None:
        self._status_updater = status_updater

    async def execute(self, job: MonitoringJobRecord) -> None:
        await self._status_updater.execute(job, MonitoringJobStatus.CRAWLING)


class LoadProcessableMonitoringJob:
    def __init__(self, repository: MonitoringJobRepository) -> None:
        self._repository = repository

    async def execute(self, job_id: UUID) -> MonitoringJobRecord | None:
        return await self._repository.get_processable(job_id, include_sources=True)
