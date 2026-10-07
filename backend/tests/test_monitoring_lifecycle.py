from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from uuid import UUID, uuid4

from app.modules.monitoring.application import (
    LoadProcessableMonitoringJob,
    StartMonitoringJob,
)
from app.modules.monitoring.domain import JobLifecycle, LifecycleState, MonitoringJobStatus
from app.shared.domain.errors import ConflictError, ValidationError


NOW = datetime(2026, 7, 21, tzinfo=UTC)


def state(status: MonitoringJobStatus, *, error: str | None = None) -> LifecycleState:
    return LifecycleState(status=status, started_at=None, finished_at=None, error_message=error)


class MonitoringLifecycleTests(TestCase):
    def test_crawling_sets_started_at(self) -> None:
        updated = JobLifecycle.transition(
            state(MonitoringJobStatus.PENDING), MonitoringJobStatus.CRAWLING, now=NOW
        )
        self.assertEqual(updated.status, MonitoringJobStatus.CRAWLING)
        self.assertEqual(updated.started_at, NOW)
        self.assertIsNone(updated.finished_at)

    def test_analysis_can_complete_and_sets_finished_at(self) -> None:
        analyzing = LifecycleState(MonitoringJobStatus.ANALYZING, NOW, None, None)
        updated = JobLifecycle.transition(analyzing, MonitoringJobStatus.COMPLETED, now=NOW)
        self.assertEqual(updated.status, MonitoringJobStatus.COMPLETED)
        self.assertEqual(updated.finished_at, NOW)

    def test_active_job_can_cancel_and_sets_finished_at(self) -> None:
        active = LifecycleState(MonitoringJobStatus.PROCESSING, NOW, None, None)
        updated = JobLifecycle.cancel(active, now=NOW)
        self.assertEqual(updated.status, MonitoringJobStatus.CANCELLED)
        self.assertEqual(updated.finished_at, NOW)

    def test_processing_can_analyze_but_cannot_complete_directly(self) -> None:
        processing = LifecycleState(MonitoringJobStatus.PROCESSING, NOW, None, None)
        analyzing = JobLifecycle.transition(processing, MonitoringJobStatus.ANALYZING, now=NOW)
        self.assertEqual(analyzing.status, MonitoringJobStatus.ANALYZING)
        with self.assertRaises(ConflictError):
            JobLifecycle.transition(processing, MonitoringJobStatus.COMPLETED, now=NOW)

    def test_failed_job_requires_error_message(self) -> None:
        with self.assertRaises(ValidationError):
            JobLifecycle.transition(
                state(MonitoringJobStatus.CRAWLING), MonitoringJobStatus.FAILED, now=NOW
            )

    def test_cancelled_and_failed_jobs_can_retry(self) -> None:
        for status in (MonitoringJobStatus.CANCELLED, MonitoringJobStatus.FAILED):
            reset = JobLifecycle.reset_for_retry(LifecycleState(status, NOW, NOW, "failure"))
            self.assertEqual(reset, LifecycleState(MonitoringJobStatus.PENDING, None, None, None))

    def test_completed_job_cannot_restart_or_cancel(self) -> None:
        completed = LifecycleState(MonitoringJobStatus.COMPLETED, NOW, NOW, None)
        with self.assertRaises(ConflictError):
            JobLifecycle.transition(completed, MonitoringJobStatus.CRAWLING, now=NOW)
        self.assertEqual(JobLifecycle.cancel(completed, now=NOW), completed)

    def test_editing_is_limited_to_pending_failed_or_cancelled_jobs(self) -> None:
        for status in (
            MonitoringJobStatus.PENDING,
            MonitoringJobStatus.FAILED,
            MonitoringJobStatus.CANCELLED,
        ):
            self.assertEqual(
                JobLifecycle.reset_for_edit(state(status)).status, MonitoringJobStatus.PENDING
            )
        with self.assertRaises(ConflictError):
            JobLifecycle.reset_for_edit(state(MonitoringJobStatus.ANALYZING))


class _StatusUpdaterSpy:
    def __init__(self) -> None:
        self.call: tuple[object, MonitoringJobStatus] | None = None

    async def execute(self, job: object, status: MonitoringJobStatus) -> None:
        self.call = (job, status)


class _ProcessableRepositorySpy:
    def __init__(self, result: object) -> None:
        self.result = result
        self.call: tuple[UUID, bool] | None = None

    async def get_processable(self, job_id: UUID, *, include_sources: bool = False) -> object:
        self.call = (job_id, include_sources)
        return self.result

    async def get_by_id(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("LoadProcessableMonitoringJob must not use get_by_id")


class MonitoringUseCaseTests(IsolatedAsyncioTestCase):
    async def test_start_requests_the_crawling_transition(self) -> None:
        updater = _StatusUpdaterSpy()
        job = object()
        await StartMonitoringJob(updater).execute(job)
        self.assertEqual(updater.call, (job, MonitoringJobStatus.CRAWLING))

    async def test_load_processable_uses_filtered_repository_query(self) -> None:
        job_id = uuid4()
        expected = object()
        repository = _ProcessableRepositorySpy(expected)
        result = await LoadProcessableMonitoringJob(repository).execute(job_id)
        self.assertIs(result, expected)
        self.assertEqual(repository.call, (job_id, True))
