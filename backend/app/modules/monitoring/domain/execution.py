"""Domain entities, value objects, and state machines for durable monitoring execution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import ClassVar
from uuid import UUID, uuid4

from app.shared.domain.errors import ConflictError, ValidationError

SOURCE_TASK_LEASE_SECONDS: int = 300
SOURCE_TASK_HEARTBEAT_SECONDS: int = 90


class ExecutionMode(str, Enum):
    LEGACY = "legacy"
    DISTRIBUTED = "distributed"


class MonitoringRunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ClaimStatus(str, Enum):
    CLAIMED = "claimed"
    ALREADY_TERMINAL = "already_terminal"
    ACTIVE_LEASE = "active_lease"
    RUN_CANCELLED = "run_cancelled"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ClaimOutcome:
    status: ClaimStatus
    claim_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    current_status: SourceTaskStatus | None = None

    @classmethod
    def claimed(cls, claim_token: UUID, lease_expires_at: datetime, attempt_count: int) -> ClaimOutcome:
        return cls(
            status=ClaimStatus.CLAIMED,
            claim_token=claim_token,
            lease_expires_at=lease_expires_at,
            attempt_count=attempt_count,
        )

    @classmethod
    def already_terminal(cls, current_status: SourceTaskStatus) -> ClaimOutcome:
        return cls(
            status=ClaimStatus.ALREADY_TERMINAL,
            current_status=current_status,
        )

    @classmethod
    def active_lease(cls, lease_expires_at: datetime) -> ClaimOutcome:
        return cls(
            status=ClaimStatus.ACTIVE_LEASE,
            lease_expires_at=lease_expires_at,
        )

    @classmethod
    def run_cancelled(cls) -> ClaimOutcome:
        return cls(status=ClaimStatus.RUN_CANCELLED)

    @classmethod
    def not_found(cls) -> ClaimOutcome:
        return cls(status=ClaimStatus.NOT_FOUND)

    @property
    def is_claimed(self) -> bool:
        return self.status == ClaimStatus.CLAIMED


@dataclass(frozen=True)
class SourceExecutionSpec:
    source_id: UUID
    topic: str
    date_from: date | None
    date_to: date | None
    crawl_config: dict[str, object]
    base_url: str
    title: str
    spec_version: int = 1
    source_count: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "source_id": str(self.source_id),
            "topic": self.topic,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "crawl_config": dict(self.crawl_config),
            "base_url": self.base_url,
            "title": self.title,
            "spec_version": self.spec_version,
            "source_count": self.source_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SourceExecutionSpec:
        d_from = data.get("date_from")
        d_to = data.get("date_to")
        raw_config = data.get("crawl_config")
        return cls(
            source_id=UUID(str(data["source_id"])),
            topic=str(data.get("topic") or ""),
            date_from=date.fromisoformat(str(d_from)) if d_from else None,
            date_to=date.fromisoformat(str(d_to)) if d_to else None,
            crawl_config=dict(raw_config) if isinstance(raw_config, dict) else {},
            base_url=str(data.get("base_url") or ""),
            title=str(data.get("title") or ""),
            spec_version=int(data.get("spec_version") or 1),
            source_count=max(1, int(data.get("source_count") or 1)),
        )


@dataclass(frozen=True)
class MonitoringRun:
    """Domain model representing a single durable execution attempt of a MonitoringJob."""

    id: UUID
    monitoring_job_id: UUID
    status: MonitoringRunStatus
    expected_sources: int
    terminal_sources: int
    successful_sources: int
    failed_sources: int
    cancelled_sources: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    execution_mode: str = "legacy"

    @property
    def is_distributed(self) -> bool:
        return self.execution_mode in (ExecutionMode.DISTRIBUTED.value, "distributed_fanout")

    _ALLOWED_TRANSITIONS: ClassVar[dict[MonitoringRunStatus, set[MonitoringRunStatus]]] = {
        MonitoringRunStatus.PENDING: {
            MonitoringRunStatus.RUNNING,
            MonitoringRunStatus.CANCELLED,
            MonitoringRunStatus.FAILED,
        },
        MonitoringRunStatus.RUNNING: {
            MonitoringRunStatus.COMPLETED,
            MonitoringRunStatus.FAILED,
            MonitoringRunStatus.CANCELLED,
        },
        MonitoringRunStatus.COMPLETED: set(),
        MonitoringRunStatus.FAILED: set(),
        MonitoringRunStatus.CANCELLED: set(),
    }

    @classmethod
    def create(
        cls,
        *,
        monitoring_job_id: UUID,
        expected_sources: int,
        run_id: UUID | None = None,
        execution_mode: str = "legacy",
        now: datetime | None = None,
    ) -> MonitoringRun:
        if expected_sources < 0:
            raise ValidationError("expected_sources cannot be negative")
        current_time = now or datetime.now(UTC)
        return cls(
            id=run_id or uuid4(),
            monitoring_job_id=monitoring_job_id,
            status=MonitoringRunStatus.PENDING,
            expected_sources=expected_sources,
            terminal_sources=0,
            successful_sources=0,
            failed_sources=0,
            cancelled_sources=0,
            created_at=current_time,
            updated_at=current_time,
            started_at=None,
            finished_at=None,
            execution_mode=execution_mode,
        )

    def transition_to(
        self,
        target: MonitoringRunStatus,
        *,
        now: datetime | None = None,
    ) -> MonitoringRun:
        if target == self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise ConflictError(
                f"Cannot transition MonitoringRun from {self.status} to {target}"
            )
        if target == MonitoringRunStatus.COMPLETED and self.terminal_sources != self.expected_sources:
            raise ValidationError(
                f"Cannot complete MonitoringRun: only {self.terminal_sources} of "
                f"{self.expected_sources} sources have reached terminal state"
            )

        current_time = now or datetime.now(UTC)
        new_started = self.started_at
        new_finished = self.finished_at

        if target == MonitoringRunStatus.RUNNING and new_started is None:
            new_started = current_time
        elif target in {
            MonitoringRunStatus.COMPLETED,
            MonitoringRunStatus.FAILED,
            MonitoringRunStatus.CANCELLED,
        }:
            new_finished = current_time

        return MonitoringRun(
            id=self.id,
            monitoring_job_id=self.monitoring_job_id,
            status=target,
            expected_sources=self.expected_sources,
            terminal_sources=self.terminal_sources,
            successful_sources=self.successful_sources,
            failed_sources=self.failed_sources,
            cancelled_sources=self.cancelled_sources,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=new_started,
            finished_at=new_finished,
            execution_mode=self.execution_mode,
        )

    def with_counters(
        self,
        *,
        terminal_delta: int = 0,
        successful_delta: int = 0,
        failed_delta: int = 0,
        cancelled_delta: int = 0,
        terminal_sources: int | None = None,
        successful_sources: int | None = None,
        failed_sources: int | None = None,
        cancelled_sources: int | None = None,
        now: datetime | None = None,
    ) -> MonitoringRun:
        new_terminal = terminal_sources if terminal_sources is not None else self.terminal_sources + terminal_delta
        new_success = successful_sources if successful_sources is not None else self.successful_sources + successful_delta
        new_failed = failed_sources if failed_sources is not None else self.failed_sources + failed_delta
        new_cancelled = cancelled_sources if cancelled_sources is not None else self.cancelled_sources + cancelled_delta

        if (
            new_terminal < 0
            or new_success < 0
            or new_failed < 0
            or new_cancelled < 0
            or new_terminal > self.expected_sources
            or (new_success + new_failed + new_cancelled) != new_terminal
        ):
            raise ValidationError(
                f"Invalid counter invariant: terminal={new_terminal}, "
                f"successful={new_success}, failed={new_failed}, cancelled={new_cancelled}, "
                f"expected={self.expected_sources}"
            )

        current_time = now or datetime.now(UTC)
        return MonitoringRun(
            id=self.id,
            monitoring_job_id=self.monitoring_job_id,
            status=self.status,
            expected_sources=self.expected_sources,
            terminal_sources=new_terminal,
            successful_sources=new_success,
            failed_sources=new_failed,
            cancelled_sources=new_cancelled,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at,
            finished_at=self.finished_at,
            execution_mode=self.execution_mode,
        )


@dataclass(frozen=True)
class SourceTask:
    """Domain model representing execution of a single source within a MonitoringRun."""

    id: UUID
    run_id: UUID
    source_id: UUID
    ordinal: int
    status: SourceTaskStatus
    documents_count: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    claim_token: UUID | None
    attempt_count: int
    error_code: str | None
    error_message: str | None
    execution_spec: SourceExecutionSpec | None = None

    _ALLOWED_TRANSITIONS: ClassVar[dict[SourceTaskStatus, set[SourceTaskStatus]]] = {
        SourceTaskStatus.PENDING: {
            SourceTaskStatus.RUNNING,
            SourceTaskStatus.CANCELLED,
        },
        SourceTaskStatus.RUNNING: {
            SourceTaskStatus.COMPLETED,
            SourceTaskStatus.FAILED,
            SourceTaskStatus.CANCELLED,
        },
        SourceTaskStatus.COMPLETED: set(),
        SourceTaskStatus.FAILED: set(),
        SourceTaskStatus.CANCELLED: set(),
    }

    @classmethod
    def create(
        cls,
        *,
        run_id: UUID,
        source_id: UUID,
        ordinal: int,
        task_id: UUID | None = None,
        execution_spec: SourceExecutionSpec | None = None,
        now: datetime | None = None,
    ) -> SourceTask:
        if ordinal < 1:
            raise ValidationError("Task ordinal must be 1 or greater")
        current_time = now or datetime.now(UTC)
        return cls(
            id=task_id or uuid4(),
            run_id=run_id,
            source_id=source_id,
            ordinal=ordinal,
            status=SourceTaskStatus.PENDING,
            documents_count=0,
            created_at=current_time,
            updated_at=current_time,
            started_at=None,
            finished_at=None,
            claimed_at=None,
            lease_expires_at=None,
            claim_token=None,
            attempt_count=0,
            error_code=None,
            error_message=None,
            execution_spec=execution_spec,
        )

    def transition_to(
        self,
        target: SourceTaskStatus,
        *,
        now: datetime | None = None,
    ) -> SourceTask:
        if target == self.status:
            return self
        if target not in self._ALLOWED_TRANSITIONS[self.status]:
            raise ConflictError(
                f"Cannot transition SourceTask from {self.status} to {target}"
            )
        if target == SourceTaskStatus.RUNNING:
            if not self.claim_token or not self.claimed_at or not self.lease_expires_at:
                raise ValidationError("Running SourceTask must have claim_token, claimed_at, and lease_expires_at")
        current_time = now or datetime.now(UTC)
        new_started = self.started_at
        new_finished = self.finished_at

        if target == SourceTaskStatus.RUNNING and new_started is None:
            new_started = current_time
        elif target in {
            SourceTaskStatus.COMPLETED,
            SourceTaskStatus.FAILED,
            SourceTaskStatus.CANCELLED,
        }:
            new_finished = current_time

        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=target,
            documents_count=self.documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=new_started,
            finished_at=new_finished,
            claimed_at=self.claimed_at,
            lease_expires_at=None if target != SourceTaskStatus.RUNNING else self.lease_expires_at,
            claim_token=None if target != SourceTaskStatus.RUNNING else self.claim_token,
            attempt_count=self.attempt_count,
            error_code=self.error_code,
            error_message=self.error_message,
            execution_spec=self.execution_spec,
        )

    def claim(
        self,
        *,
        claim_token: UUID | None = None,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> SourceTask:
        current_time = now or datetime.now(UTC)
        token = claim_token or uuid4()
        if self.status == SourceTaskStatus.PENDING:
            new_attempt = 1
        elif self.status == SourceTaskStatus.RUNNING:
            if self.lease_expires_at is not None and self.lease_expires_at > current_time:
                raise ConflictError("Cannot claim SourceTask whose lease has not expired")
            new_attempt = self.attempt_count + 1
        else:
            raise ConflictError(f"Cannot claim SourceTask in status {self.status}")

        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=SourceTaskStatus.RUNNING,
            documents_count=self.documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at or current_time,
            finished_at=None,
            claimed_at=current_time,
            lease_expires_at=current_time + timedelta(seconds=lease_duration_seconds),
            claim_token=token,
            attempt_count=new_attempt,
            error_code=None,
            error_message=None,
            execution_spec=self.execution_spec,
        )

    def renew_lease(
        self,
        *,
        claim_token: UUID,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> SourceTask:
        if self.status != SourceTaskStatus.RUNNING:
            raise ConflictError(f"Cannot renew lease for task in status {self.status}")
        if not claim_token or self.claim_token != claim_token:
            raise ConflictError("Stale or missing claim token: cannot renew lease")
        current_time = now or datetime.now(UTC)
        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=self.status,
            documents_count=self.documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at,
            finished_at=self.finished_at,
            claimed_at=self.claimed_at,
            lease_expires_at=current_time + timedelta(seconds=lease_duration_seconds),
            claim_token=self.claim_token,
            attempt_count=self.attempt_count,
            error_code=self.error_code,
            error_message=self.error_message,
            execution_spec=self.execution_spec,
        )

    def complete(
        self,
        documents_count: int,
        *,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> SourceTask:
        if self.status != SourceTaskStatus.RUNNING:
            raise ConflictError(f"Cannot complete SourceTask from status {self.status}")
        if not claim_token or self.claim_token != claim_token:
            raise ConflictError("Stale or missing claim token: cannot complete task")
        if documents_count < 0:
            raise ValidationError("documents_count cannot be negative")
        current_time = now or datetime.now(UTC)
        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=SourceTaskStatus.COMPLETED,
            documents_count=documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at,
            finished_at=current_time,
            claimed_at=self.claimed_at,
            lease_expires_at=None,
            claim_token=None,
            attempt_count=self.attempt_count,
            error_code=None,
            error_message=None,
            execution_spec=self.execution_spec,
        )

    def fail(
        self,
        error_code: str,
        error_message: str,
        *,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> SourceTask:
        if self.status != SourceTaskStatus.RUNNING:
            raise ConflictError(f"Cannot fail SourceTask from status {self.status}")
        if not claim_token or self.claim_token != claim_token:
            raise ConflictError("Stale or missing claim token: cannot fail task")
        current_time = now or datetime.now(UTC)
        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=SourceTaskStatus.FAILED,
            documents_count=self.documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at,
            finished_at=current_time,
            claimed_at=self.claimed_at,
            lease_expires_at=None,
            claim_token=None,
            attempt_count=self.attempt_count,
            error_code=error_code,
            error_message=error_message,
            execution_spec=self.execution_spec,
        )

    def cancel(self, *, now: datetime | None = None) -> SourceTask:
        if self.status in {
            SourceTaskStatus.COMPLETED,
            SourceTaskStatus.FAILED,
            SourceTaskStatus.CANCELLED,
        }:
            return self
        current_time = now or datetime.now(UTC)
        return SourceTask(
            id=self.id,
            run_id=self.run_id,
            source_id=self.source_id,
            ordinal=self.ordinal,
            status=SourceTaskStatus.CANCELLED,
            documents_count=self.documents_count,
            created_at=self.created_at,
            updated_at=current_time,
            started_at=self.started_at,
            finished_at=current_time,
            claimed_at=self.claimed_at,
            lease_expires_at=None,
            claim_token=None,
            attempt_count=self.attempt_count,
            error_code="CANCELLED",
            error_message="Task cancelled by user or job cancellation",
            execution_spec=self.execution_spec,
        )
