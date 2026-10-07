"""Unit tests for MonitoringRun and SourceTask domain models and lifecycles."""

from datetime import UTC, datetime, timedelta
from unittest import TestCase
from uuid import uuid4

from app.modules.monitoring.domain.execution import (
    MonitoringRun,
    MonitoringRunStatus,
    SourceTask,
    SourceTaskStatus,
)
from app.shared.domain.errors import ConflictError, ValidationError


class MonitoringRunDomainTests(TestCase):
    def test_run_creation_defaults(self) -> None:
        job_id = uuid4()
        run = MonitoringRun.create(monitoring_job_id=job_id, expected_sources=5)
        self.assertEqual(run.status, MonitoringRunStatus.PENDING)
        self.assertEqual(run.expected_sources, 5)
        self.assertEqual(run.terminal_sources, 0)
        self.assertEqual(run.successful_sources, 0)
        self.assertEqual(run.failed_sources, 0)
        self.assertEqual(run.cancelled_sources, 0)
        self.assertIsNone(run.started_at)
        self.assertIsNone(run.finished_at)

    def test_negative_expected_sources_raises_error(self) -> None:
        with self.assertRaises(ValidationError):
            MonitoringRun.create(monitoring_job_id=uuid4(), expected_sources=-1)

    def test_run_lifecycle_transitions(self) -> None:
        run = MonitoringRun.create(monitoring_job_id=uuid4(), expected_sources=3)
        now = datetime.now(UTC)

        # PENDING -> RUNNING
        running = run.transition_to(MonitoringRunStatus.RUNNING, now=now)
        self.assertEqual(running.status, MonitoringRunStatus.RUNNING)
        self.assertEqual(running.started_at, now)
        self.assertIsNone(running.finished_at)

        # RUNNING -> COMPLETED fails if terminal_sources != expected_sources
        with self.assertRaises(ValidationError):
            running.transition_to(MonitoringRunStatus.COMPLETED)

        # RUNNING -> COMPLETED succeeds when terminal_sources == expected_sources
        with_counters = running.with_counters(terminal_sources=3, successful_sources=2, failed_sources=1)
        now_done = datetime.now(UTC)
        completed = with_counters.transition_to(MonitoringRunStatus.COMPLETED, now=now_done)
        self.assertEqual(completed.status, MonitoringRunStatus.COMPLETED)
        self.assertEqual(completed.finished_at, now_done)

        # Terminal state cannot transition to non-terminal
        with self.assertRaises(ConflictError):
            completed.transition_to(MonitoringRunStatus.RUNNING)

        # Idempotent transition to same terminal status returns self
        same = completed.transition_to(MonitoringRunStatus.COMPLETED)
        self.assertEqual(same.finished_at, now_done)

    def test_run_invalid_direct_transition_raises_conflict(self) -> None:
        run = MonitoringRun.create(monitoring_job_id=uuid4(), expected_sources=3)
        with self.assertRaises(ConflictError):
            run.transition_to(MonitoringRunStatus.COMPLETED)

    def test_run_counter_invariants(self) -> None:
        run = MonitoringRun.create(monitoring_job_id=uuid4(), expected_sources=5)
        updated = run.with_counters(
            terminal_sources=3, successful_sources=2, failed_sources=1, cancelled_sources=0
        )
        self.assertEqual(updated.terminal_sources, 3)
        self.assertEqual(updated.successful_sources, 2)
        self.assertEqual(updated.failed_sources, 1)
        self.assertEqual(updated.cancelled_sources, 0)

        # Sum of parts exceeding terminal_sources raises ValidationError
        with self.assertRaises(ValidationError):
            run.with_counters(terminal_sources=2, successful_sources=2, failed_sources=1)

        # Terminal exceeding expected raises ValidationError
        with self.assertRaises(ValidationError):
            run.with_counters(terminal_sources=6, successful_sources=5, failed_sources=1)


class SourceTaskDomainTests(TestCase):
    def test_task_creation_defaults(self) -> None:
        run_id = uuid4()
        source_id = uuid4()
        task = SourceTask.create(run_id=run_id, source_id=source_id, ordinal=1)
        self.assertEqual(task.status, SourceTaskStatus.PENDING)
        self.assertEqual(task.ordinal, 1)
        self.assertEqual(task.documents_count, 0)
        self.assertEqual(task.attempt_count, 0)
        self.assertIsNone(task.started_at)
        self.assertIsNone(task.finished_at)
        self.assertIsNone(task.claimed_at)
        self.assertIsNone(task.lease_expires_at)
        self.assertIsNone(task.claim_token)

    def test_ordinal_zero_or_negative_raises_error(self) -> None:
        with self.assertRaises(ValidationError):
            SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=0)

    def test_task_lifecycle_happy_path(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        now_start = datetime.now(UTC)
        token = uuid4()
        claimed = task.claim(claim_token=token, lease_duration_seconds=300, now=now_start)
        self.assertEqual(claimed.status, SourceTaskStatus.RUNNING)
        self.assertEqual(claimed.started_at, now_start)
        self.assertEqual(claimed.claimed_at, now_start)
        self.assertEqual(claimed.claim_token, token)
        self.assertEqual(claimed.attempt_count, 1)
        self.assertEqual(claimed.lease_expires_at, now_start + timedelta(seconds=300))

        # Stale token completion fails
        with self.assertRaises(ConflictError):
            claimed.complete(documents_count=12, claim_token=uuid4())

        now_done = datetime.now(UTC)
        completed = claimed.complete(documents_count=12, claim_token=token, now=now_done)
        self.assertEqual(completed.status, SourceTaskStatus.COMPLETED)
        self.assertEqual(completed.documents_count, 12)
        self.assertEqual(completed.finished_at, now_done)

        # Terminal state cannot be claimed
        with self.assertRaises(ConflictError):
            completed.claim()

    def test_task_lifecycle_failure_path(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=2)
        token = uuid4()
        claimed = task.claim(claim_token=token)
        now_fail = datetime.now(UTC)
        failed = claimed.fail("TIMEOUT", "Connection timed out after 180s", claim_token=token, now=now_fail)
        self.assertEqual(failed.status, SourceTaskStatus.FAILED)
        self.assertEqual(failed.error_code, "TIMEOUT")
        self.assertEqual(failed.error_message, "Connection timed out after 180s")
        self.assertEqual(failed.finished_at, now_fail)

    def test_task_reclaim_after_lease_expiry(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        t0 = datetime.now(UTC) - timedelta(seconds=400)
        token1 = uuid4()
        claimed1 = task.claim(claim_token=token1, lease_duration_seconds=300, now=t0)
        self.assertEqual(claimed1.attempt_count, 1)

        # Reclaim with fresh token
        t1 = datetime.now(UTC)
        token2 = uuid4()
        reclaimed = claimed1.claim(claim_token=token2, lease_duration_seconds=300, now=t1)
        self.assertEqual(reclaimed.status, SourceTaskStatus.RUNNING)
        self.assertEqual(reclaimed.attempt_count, 2)
        self.assertEqual(reclaimed.claim_token, token2)

    def test_task_reclaim_before_lease_expiry_fails(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        t0 = datetime.now(UTC)
        claimed = task.claim(lease_duration_seconds=300, now=t0)
        with self.assertRaises(ConflictError):
            claimed.claim(now=t0 + timedelta(seconds=100))

    def test_task_cancellation_from_pending(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=3)
        now = datetime.now(UTC)
        cancelled = task.cancel(now=now)
        self.assertEqual(cancelled.status, SourceTaskStatus.CANCELLED)
        self.assertEqual(cancelled.finished_at, now)

    def test_lease_duration_constant_and_margin(self) -> None:
        from app.modules.monitoring.domain.execution import SOURCE_TASK_LEASE_SECONDS
        self.assertEqual(SOURCE_TASK_LEASE_SECONDS, 300)
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        now = datetime.now(UTC)
        claimed = task.claim(now=now)
        self.assertEqual(claimed.lease_expires_at, now + timedelta(seconds=300))
        self.assertTrue(claimed.lease_expires_at > claimed.claimed_at)

    def test_task_running_invariant_requires_claim_token_and_timestamps(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        with self.assertRaises(ValidationError):
            task.transition_to(SourceTaskStatus.RUNNING)

    def test_task_complete_stale_or_missing_claim_token_fails(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        token = uuid4()
        claimed = task.claim(claim_token=token)
        with self.assertRaises(ConflictError):
            claimed.complete(documents_count=5, claim_token=uuid4())

    def test_task_fail_stale_or_missing_claim_token_fails(self) -> None:
        task = SourceTask.create(run_id=uuid4(), source_id=uuid4(), ordinal=1)
        token = uuid4()
        claimed = task.claim(claim_token=token)
        with self.assertRaises(ConflictError):
            claimed.fail(error_code="TIMEOUT", error_message="msg", claim_token=uuid4())
