"""Repository port protocols for MonitoringRun and SourceTask durable execution."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.modules.monitoring.domain.execution import (
    SOURCE_TASK_HEARTBEAT_SECONDS,
    SOURCE_TASK_LEASE_SECONDS,
    ClaimOutcome,
    ClaimStatus,
    MonitoringRun,
    MonitoringRunStatus,
    SourceExecutionSpec,
    SourceTask,
    SourceTaskStatus,
)


class MonitoringRunRepositoryPort(Protocol):
    """Port for MonitoringRun persistence operations."""

    async def create(self, run: MonitoringRun) -> MonitoringRun:
        """Persist a new MonitoringRun."""
        ...

    async def get_by_id(self, run_id: UUID) -> MonitoringRun | None:
        """Find a MonitoringRun by its ID."""
        ...

    async def get_active_for_job(self, job_id: UUID) -> MonitoringRun | None:
        """Find the active (pending/running) run for a job, if any."""
        ...

    async def ensure_active_run(
        self,
        job_id: UUID,
        *,
        expected_sources: int,
        execution_mode: str = "legacy",
        now: datetime | None = None,
    ) -> MonitoringRun:
        """Idempotently and race-safely return an active run for the job, creating one if needed."""
        ...

    async def finalize_run(
        self,
        run_id: UUID,
        target_status: MonitoringRunStatus = MonitoringRunStatus.COMPLETED,
        *,
        now: datetime | None = None,
    ) -> MonitoringRun | None:
        """Idempotently complete a run, strictly requiring terminal_sources == expected_sources."""
        ...

    async def transition_status(
        self,
        run_id: UUID,
        target_status: MonitoringRunStatus,
        now: datetime | None = None,
    ) -> MonitoringRun | None:
        """Transition run status with timestamp updates."""
        ...

    async def update_counters(
        self,
        run_id: UUID,
        *,
        terminal_delta: int = 0,
        successful_delta: int = 0,
        success_delta: int | None = None,
        failed_delta: int = 0,
        cancelled_delta: int = 0,
        now: datetime | None = None,
    ) -> tuple[int, int, int]:
        """Atomically update run source execution counters."""
        ...


class SourceTaskRepositoryPort(Protocol):
    """Port for SourceTask persistence operations."""

    async def defer_task(self, task_id: UUID, *, claim_token: UUID, delay_seconds: int = 2) -> bool:
        """Fenced release plus durable continuation intent, without terminal counters."""
        ...

    async def ensure_tasks_for_sources(
        self,
        run_id: UUID,
        sources: Sequence[tuple[UUID, int]] | Sequence[dict[str, object]] | list,
    ) -> list[SourceTask]:
        """Idempotently create SourceTask records for a run's sources."""
        ...

    async def get_by_id(self, task_id: UUID) -> SourceTask | None:
        """Find a SourceTask by its ID."""
        ...

    async def get_by_run_and_source(self, run_id: UUID, source_id: UUID) -> SourceTask | None:
        """Find a SourceTask by run ID and source ID."""
        ...

    async def list_for_run(self, run_id: UUID) -> list[SourceTask]:
        """List all SourceTasks for a run ordered by ordinal."""
        ...

    async def claim_task(
        self,
        task_id: UUID,
        *,
        claim_token: UUID | None = None,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> tuple[bool, UUID | None]:
        """Atomically claim a task if PENDING or RUNNING with expired lease."""
        ...

    async def claim_task_explicit(
        self,
        task_id: UUID,
        *,
        claim_token: UUID | None = None,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> ClaimOutcome:
        """Atomically claim a task and return typed ClaimOutcome."""
        ...

    async def renew_lease(
        self,
        task_id: UUID,
        *,
        claim_token: UUID,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        """Extend lease expiration for a running task if claim token matches."""
        ...

    async def complete_task_and_update_run(
        self,
        task_id: UUID,
        *,
        documents_count: int,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> bool:
        """Atomically complete task with matching claim token and update run counters."""
        ...

    async def fail_task_and_update_run(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> bool:
        """Atomically fail task with matching claim token and update run counters."""
        ...

    async def complete_task(
        self,
        task_id: UUID,
        *,
        documents_count: int,
        now: datetime | None = None,
    ) -> bool:
        """Mark a SourceTask as completed with document count."""
        ...

    async def fail_task(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> bool:
        """Mark a SourceTask as failed with error details."""
        ...

    async def cancel_task(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> bool:
        """Mark a SourceTask as cancelled."""
        ...


class SourceExecutionEnginePort(Protocol):
    """Port for in-memory crawl execution and document extraction."""

    async def execute_crawl(
        self,
        spec: SourceExecutionSpec,
        *, checkpoint=None,
    ) -> tuple[object, object, object]:
        """Execute crawl and document extraction in-memory without database mutations."""
        ...


class SourceResultPersisterPort(Protocol):
    """Port for fenced transactional persistence of source results."""

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
        """Atomically persist documents, update source health, complete task, and update run counters."""
        ...

    async def persist_failure(
        self,
        task_id: UUID,
        claim_token: UUID,
        job_id: UUID,
        source_id: UUID,
        error: Exception,
    ) -> bool:
        """Atomically record failure diagnostics, fail task, and update run counters."""
        ...


class SourceTaskDispatcherPort(Protocol):
    """Port for dispatching source task execution messages."""

    def dispatch(self, source_task_id: UUID) -> None:
        """Dispatch a message to process the given source task."""
        ...


# Backward compatibility aliases
MonitoringRunRepository = MonitoringRunRepositoryPort
SourceTaskRepository = SourceTaskRepositoryPort

__all__ = [
    "SOURCE_TASK_HEARTBEAT_SECONDS",
    "SOURCE_TASK_LEASE_SECONDS",
    "ClaimOutcome",
    "ClaimStatus",
    "MonitoringRunRepository",
    "MonitoringRunRepositoryPort",
    "SourceExecutionEnginePort",
    "SourceExecutionSpec",
    "SourceResultPersisterPort",
    "SourceTaskDispatcherPort",
    "SourceTaskRepository",
    "SourceTaskRepositoryPort",
]
