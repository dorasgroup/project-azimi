"""Typed monitoring boundary contracts."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol, TypeVar, runtime_checkable
from uuid import UUID

from app.modules.monitoring.ports.execution import (
    MonitoringRunRepository,
    SourceTaskRepository,
)


class RoleValue(Protocol):
    value: str


class StatusValue(Protocol):
    value: str


class MonitoringSourceRecord(Protocol):
    id: UUID
    title: str


class MonitoringReportRecord(Protocol):
    pdf_path: str | None
    docx_path: str | None


class MonitoringJobRecord(Protocol):
    id: UUID
    topic: str
    description: str | None
    date_from_jalali: str
    date_to_jalali: str
    date_from_gregorian: date
    date_to_gregorian: date
    bot_identity_id: UUID | None
    created_at: datetime
    status: StatusValue
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    sources: list[MonitoringSourceRecord]
    report: MonitoringReportRecord | None


@dataclass(frozen=True)
class MonitoringJobDraft:
    topic: str
    description: str | None
    date_from_jalali: str
    date_to_jalali: str
    date_from_gregorian: date
    date_to_gregorian: date
    source_ids: list[UUID]
    bot_identity_id: UUID | None
    requester_id: UUID


@runtime_checkable
class MonitoringJobRepository(Protocol):
    async def create(self, draft: MonitoringJobDraft) -> MonitoringJobRecord: ...

    async def get_by_id(
        self, job_id: UUID, *, include_sources: bool = False
    ) -> MonitoringJobRecord | None: ...

    async def get_processable(
        self, job_id: UUID, *, include_sources: bool = False
    ) -> MonitoringJobRecord | None: ...

    async def get_accessible_by_id(
        self,
        job_id: UUID,
        *,
        requester_id: UUID | None,
        is_super_admin: bool,
        include_report: bool = False,
    ) -> MonitoringJobRecord | None: ...

    async def list_for_actor(
        self, *, page: int, page_size: int, requester_id: UUID | None, is_super_admin: bool
    ) -> tuple[list[MonitoringJobRecord], int]: ...

    async def default_identity_id(self) -> UUID: ...

    async def active_sources(self, source_ids: list[UUID]) -> list[MonitoringSourceRecord]: ...

    async def delete(self, job: MonitoringJobRecord) -> None: ...

    async def save(self) -> None: ...


class MonitoringJobQueue(Protocol):
    def enqueue(self, job_id: UUID) -> None: ...


class MonitoringJobOutputCleaner(Protocol):
    async def clear(self, job: MonitoringJobRecord) -> None: ...

    async def clear_files(self, job: MonitoringJobRecord) -> None: ...


class BotIdentityRecord(Protocol):
    id: UUID
    title: str
    description: str | None
    role_definition: str
    tone: str | None
    analysis_style: str | None
    cognitive_framework: str | None
    output_structure: str | None
    source_policy: str | None
    is_active: bool
    is_default: bool


@runtime_checkable
class BotIdentityRepository(Protocol):
    async def list(self, page: int, page_size: int) -> tuple[list[BotIdentityRecord], int]: ...

    async def get(self, identity_id: UUID) -> BotIdentityRecord | None: ...

    async def has_default(self) -> bool: ...

    async def active_default(self) -> BotIdentityRecord | None: ...

    async def set_default(self, identity: BotIdentityRecord) -> None: ...

    async def delete_with_jobs(self, identity: BotIdentityRecord) -> None: ...

    async def add(self, identity: BotIdentityRecord) -> None: ...

    async def flush(self) -> None: ...


class SystemLogRecord(Protocol):
    id: UUID
    module: str
    message: str


@runtime_checkable
class SystemLogRepository(Protocol):
    async def record(self, log: SystemLogRecord) -> SystemLogRecord: ...

    async def delete_resolved(self) -> int: ...


ReadResult_co = TypeVar("ReadResult_co", covariant=True)


@runtime_checkable
class DashboardReader(Protocol[ReadResult_co]):
    async def summary(self, *, user_id: UUID, role: RoleValue) -> ReadResult_co: ...


@runtime_checkable
class SystemLogReader(Protocol[ReadResult_co]):
    async def list(
        self,
        page: int,
        page_size: int,
        *,
        level: StatusValue | None = None,
        module: str | None = None,
    ) -> ReadResult_co: ...

from app.modules.monitoring.ports.operations import BotIdentityCommands, BotIdentityQueries, DashboardQueries, SystemLogOperations

__all__ = [
    "BotIdentityCommands",
    "BotIdentityQueries",
    "BotIdentityRecord",
    "BotIdentityRepository",
    "DashboardQueries",
    "DashboardReader",
    "MonitoringJobDraft",
    "MonitoringJobOutputCleaner",
    "MonitoringJobQueue",
    "MonitoringJobRecord",
    "MonitoringJobRepository",
    "MonitoringReportRecord",
    "MonitoringRunRepository",
    "MonitoringSourceRecord",
    "RoleValue",
    "SourceTaskRepository",
    "StatusValue",
    "SystemLogOperations",
    "SystemLogReader",
    "SystemLogRecord",
    "SystemLogRepository",
]
