"""Framework-independent contracts for source crawling."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping


@dataclass(frozen=True)
class SourceProbeResult:
    requested_url: str
    final_url: str | None
    success: bool
    http_status: int | None
    response_time_ms: int | None
    content_type: str | None
    redirected: bool
    robots_allowed: bool | None
    reason: str | None


@dataclass(frozen=True)
class DiscoveredLinkRecord:
    url: str
    title: str | None
    discovered_via: str
    published_at: datetime | None
    topic_matches: tuple[str, ...] = ()
    discovery_methods: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrawledPageRecord:
    url: str
    final_url: str | None
    success: bool
    status_code: int | None
    title: str | None
    html: str | None
    markdown: str | None
    content_hash: str | None
    cache_status: str | None
    error: str | None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CrawlRequest:
    source_url: str
    topic: str
    date_from: date | None
    date_to: date | None
    policy: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class CrawlOutcome:
    source_url: str
    probe: SourceProbeResult
    discovered_links: tuple[DiscoveredLinkRecord, ...]
    pages: tuple[CrawledPageRecord, ...]
    started_at: datetime
    finished_at: datetime
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, object] = field(default_factory=dict)
