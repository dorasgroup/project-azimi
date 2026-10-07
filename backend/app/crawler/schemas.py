from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator


class SourceDiscoveryProfile(BaseModel):
    replace_default_seeds: bool = False
    feed_paths: list[str] = Field(default_factory=list, max_length=20)
    sitemap_paths: list[str] = Field(default_factory=list, max_length=50)
    search_templates: list[str] = Field(default_factory=list, max_length=20)
    archive_templates: list[str] = Field(default_factory=list, max_length=30)
    include_url_globs: list[str] = Field(default_factory=list, max_length=30)
    exclude_url_globs: list[str] = Field(default_factory=list, max_length=30)

    @field_validator(
        "feed_paths",
        "sitemap_paths",
        "search_templates",
        "archive_templates",
        "include_url_globs",
        "exclude_url_globs",
    )
    @classmethod
    def validate_profile_values(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 500:
                raise ValueError("Source profile values must be 1 to 500 characters")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned

    @field_validator("search_templates")
    @classmethod
    def validate_search_templates(cls, values: list[str]) -> list[str]:
        if any("{query}" not in value for value in values):
            raise ValueError("Every search template must contain {query}")
        return values

    @field_validator("archive_templates")
    @classmethod
    def validate_archive_templates(cls, values: list[str]) -> list[str]:
        allowed = {"{year}", "{month}", "{jalali_year}", "{jalali_month}"}
        for value in values:
            remainder = value
            for placeholder in allowed:
                remainder = remainder.replace(placeholder, "")
            if "{" in remainder or "}" in remainder:
                raise ValueError("Archive template contains an unsupported placeholder")
        return values


class CrawlPolicy(BaseModel):
    max_pages: int = Field(default=80, ge=1, le=250)
    max_concurrency: int = Field(default=3, ge=1, le=10)
    page_timeout_ms: int = Field(default=30000, ge=5000, le=120000)
    probe_timeout_seconds: float = Field(default=15, gt=0, le=60)
    discovery_timeout_seconds: float = Field(default=60, gt=0, le=120)
    historical_timeout_seconds: float = Field(default=15, gt=0, le=60)
    fetch_batch_timeout_seconds: float = Field(default=90, gt=0, le=120)
    page_batch_size: int = Field(default=20, ge=1, le=25)
    page_attempt_timeout_seconds: float = Field(default=10, gt=0, le=30)
    respect_robots_txt: bool = True
    allow_subdomains: bool = False
    render_javascript: bool = False
    use_stealth: bool = False
    retry_count: int = Field(default=2, ge=0, le=3)
    max_sitemaps: int = Field(default=120, ge=1, le=1000)
    max_sitemap_depth: int = Field(default=4, ge=1, le=10)
    max_discovery_pages: int = Field(default=4, ge=1, le=20)
    max_archive_seeds: int = Field(default=18, ge=0, le=100)
    max_discovered_urls: int = Field(default=5000, ge=100, le=50000)
    browser_fallback_enabled: bool = True
    browser_timeout_ms: int = Field(default=60000, ge=5000, le=180000)
    http_min_content_length: int = Field(default=500, ge=100, le=10000)
    http_min_text_length: int = Field(default=160, ge=20, le=5000)
    request_delay_ms: int = Field(default=100, ge=0, le=10000)
    retry_backoff_base_ms: int = Field(default=250, ge=0, le=10000)
    retry_backoff_max_ms: int = Field(default=3000, ge=0, le=60000)
    circuit_breaker_threshold: int = Field(default=5, ge=1, le=100)
    circuit_breaker_cooldown_seconds: int = Field(default=30, ge=1, le=3600)
    http_cache_enabled: bool = True
    historical_discovery_enabled: bool = True
    gdelt_discovery_enabled: bool = True
    common_crawl_discovery_enabled: bool = True
    historical_fallback_min_results: int = Field(default=12, ge=0, le=250)
    max_external_results: int = Field(default=120, ge=1, le=500)
    common_crawl_max_indexes: int = Field(default=4, ge=1, le=12)
    discovery_profile: SourceDiscoveryProfile = Field(default_factory=SourceDiscoveryProfile)
    candidate_diversity_enabled: bool = True
    candidate_provider_slots: int = Field(default=4, ge=0, le=20)
    candidate_repeat_provider_penalty: int = Field(default=20, ge=0, le=200)


class SourceProbe(BaseModel):
    requested_url: AnyHttpUrl
    final_url: AnyHttpUrl | None = None
    success: bool
    retryable: bool = False
    http_status: int | None = None
    response_time_ms: int | None = None
    content_type: str | None = None
    redirected: bool = False
    robots_allowed: bool | None = None
    reason: str | None = None


class DiscoveredLink(BaseModel):
    url: AnyHttpUrl
    title: str | None = None
    discovered_via: str
    published_at: datetime | None = None
    topic_matches: list[str] = Field(default_factory=list)
    discovery_methods: list[str] = Field(default_factory=list)


class CrawledPage(BaseModel):
    url: AnyHttpUrl
    final_url: AnyHttpUrl | None = None
    success: bool
    status_code: int | None = None
    title: str | None = None
    html: str | None = None
    markdown: str | None = None
    content_hash: str | None = None
    cache_status: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrawlDiagnostics(BaseModel):
    status: str = "not_started"
    reason_code: str = "not_started"
    summary: str = "خزش هنوز اجرا نشده است."
    provider_counts: dict[str, int] = Field(default_factory=dict)
    selected_provider_counts: dict[str, int] = Field(default_factory=dict)
    discovered_total: int = 0
    selected_total: int = 0
    rejected_by_date: int = 0
    skipped_by_limit: int = 0
    fetched_total: int = 0
    successful_pages: int = 0
    failed_pages: int = 0
    pending_pages: int = 0
    retryable_pages: int = 0
    resumed_pages: int = 0
    selected_completion_percent: float = 0
    fetch_strategy_counts: dict[str, int] = Field(default_factory=dict)
    failure_counts: dict[str, int] = Field(default_factory=dict)
    http_status_counts: dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0


class CrawlBatchResult(BaseModel):
    source_url: AnyHttpUrl
    probe: SourceProbe
    discovered_links: list[DiscoveredLink]
    pages: list[CrawledPage]
    started_at: datetime
    finished_at: datetime
    diagnostics: CrawlDiagnostics = Field(default_factory=CrawlDiagnostics)
