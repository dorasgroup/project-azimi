"""Typed crawling boundary contracts."""

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.crawling.domain import CrawlOutcome, CrawlRequest, SourceProbeResult


class SourceCrawler(Protocol):
    async def crawl(self, request: CrawlRequest) -> CrawlOutcome: ...

    async def probe(self, source_url: str) -> SourceProbeResult: ...


class SourceRecord(Protocol):
    id: UUID
    title: str
    base_url: str
    type: str
    is_active: bool
    crawl_config: dict[str, object]
    last_status: str
    last_status_message: str | None
    last_http_status: int | None
    last_response_time_ms: int | None
    last_checked_at: datetime | None


@runtime_checkable
class SourceRepository(Protocol):
    async def list(self, page: int, page_size: int, *, operational_only: bool = False) -> tuple[list[SourceRecord], int]: ...

    async def get(self, source_id: UUID) -> SourceRecord | None: ...

    async def add(self, source: SourceRecord) -> None: ...

    async def delete(self, source: SourceRecord) -> None: ...

    async def flush(self) -> None: ...

from app.modules.crawling.ports.operations import SourceCommands, SourceQueries
