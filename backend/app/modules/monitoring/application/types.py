"""Application types and data transfer objects for monitoring execution."""

from dataclasses import dataclass
from datetime import date
from typing import TypedDict
from uuid import UUID


class MonitoringSourceSnapshot(TypedDict):
    id: UUID
    title: str
    base_url: str
    crawl_config: dict[str, object]


@dataclass(frozen=True)
class MonitoringExecutionContext:
    job_id: UUID
    topic: str
    description: str | None
    date_from: date
    date_to: date
    sources: list[MonitoringSourceSnapshot]
    run_id: UUID | None = None
    execution_mode: str | None = None


@dataclass(frozen=True)
class SourceProcessingResult:
    pages: int
    documents: int
