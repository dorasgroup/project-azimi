"""Typed source capabilities used to plan URL discovery."""

from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.crawling.domain.crawl_policy import CrawlPolicy, SourceDiscoveryProfile


class DiscoveryMethod(StrEnum):
    FEED = "feed"
    SITEMAP = "sitemap"
    HTML = "html"
    SEARCH = "search"
    ARCHIVE = "archive"
    GDELT = "gdelt"
    COMMON_CRAWL = "common_crawl"


@dataclass(frozen=True)
class SourceCrawlProfile:
    """Static, side-effect-free knowledge about one source family."""

    key: str
    canonical_host: str
    aliases: tuple[str, ...] = ()
    languages: tuple[str, ...] = ("fa",)
    methods: tuple[DiscoveryMethod, ...] = ()
    feed_paths: tuple[str, ...] = ()
    sitemap_paths: tuple[str, ...] = ()
    search_templates: tuple[str, ...] = ()
    archive_templates: tuple[str, ...] = ()
    requires_browser: bool = False
    content_restricted: bool = False
    remote_egress_required: bool = False

    @property
    def hosts(self) -> tuple[str, ...]:
        return (self.canonical_host, *self.aliases)


@dataclass(frozen=True)
class SourceDiscoveryPlan:
    """Resolved profile plus the domain crawl policy consumed by providers."""

    profile: SourceCrawlProfile
    policy: CrawlPolicy
    enabled: tuple[DiscoveryMethod, ...]
    skipped: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCapabilitySnapshot:
    """Observed network capabilities for one source at a point in time."""

    profile_key: str
    source_url: str
    final_url: str | None
    http_reachable: bool
    http_status: int | None
    redirected: bool
    robots_reachable: bool
    feed_reachable: bool
    sitemap_reachable: bool
    requires_browser: bool
    content_restricted: bool
    remote_egress_required: bool
    evidence: dict[str, str] = field(default_factory=dict)


@dataclass
class DiscoveryStrategyTrace:
    """Bounded diagnostics for one discovery execution."""

    profile_key: str
    attempted: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    provider_counts: dict[str, int] = field(default_factory=dict)
    fallbacks_used: list[str] = field(default_factory=list)
    query_languages: list[str] = field(default_factory=list)
    retrieval_queries: list[str] = field(default_factory=list)
    recommended_action: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "profile_key": self.profile_key,
            "attempted": self.attempted,
            "skipped": self.skipped,
            "provider_counts": self.provider_counts,
            "fallbacks_used": self.fallbacks_used,
            "query_languages": self.query_languages,
            "retrieval_queries": self.retrieval_queries,
            "recommended_action": self.recommended_action,
        }
