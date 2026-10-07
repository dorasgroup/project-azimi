"""Date-aware crawl limits for monitoring jobs."""

from datetime import date
from pydantic import BaseModel, Field, field_validator


class MonitoringDiscoveryProfile(BaseModel):
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
                raise ValueError(
                    "Archive template contains unsupported placeholders. "
                    "Allowed: {year}, {month}, {jalali_year}, {jalali_month}"
                )
        return values


class MonitoringCrawlPolicy(BaseModel):
    """Monitoring domain policy for bounded per-source crawl execution."""

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
    discovery_profile: MonitoringDiscoveryProfile = Field(
        default_factory=MonitoringDiscoveryProfile
    )
    candidate_diversity_enabled: bool = True
    candidate_provider_slots: int = Field(default=4, ge=0, le=20)
    candidate_repeat_provider_penalty: int = Field(default=20, ge=0, le=200)



# Alias for backward compatibility if any callers use CrawlPolicy from monitoring domain
CrawlPolicy = MonitoringCrawlPolicy


def effective_crawl_policy(
    source_config: dict, *, date_from: date, date_to: date, source_count: int
) -> MonitoringCrawlPolicy:
    configured = MonitoringCrawlPolicy.model_validate(source_config.get("crawl_policy", {}))
    range_days = max(1, (date_to - date_from).days + 1)
    if range_days <= 31:
        required_pages = 25
    elif range_days <= 180:
        required_pages = 50
    elif range_days <= 370:
        required_pages = 90
    else:
        required_pages = 120
    if source_count >= 10 and range_days >= 180:
        required_pages = max(required_pages, 100)

    # Short, multi-source monitoring jobs need bounded per-source work so that
    # one broad job does not scale linearly to thousands of page fetches.
    if range_days <= 31:
        if source_count >= 20:
            short_range_cap = 25
        elif source_count >= 10:
            short_range_cap = 30
        elif source_count >= 4:
            short_range_cap = 50
        else:
            short_range_cap = 80
        effective_max_pages = min(
            250,
            short_range_cap,
            max(configured.max_pages, required_pages),
        )
    else:
        effective_max_pages = min(250, max(configured.max_pages, required_pages))
    if source_count >= 20:
        discovered_url_cap = 1500
    elif source_count >= 10:
        discovered_url_cap = 2500
    elif source_count >= 4:
        discovered_url_cap = 3500
    else:
        discovered_url_cap = configured.max_discovered_urls
    return configured.model_copy(
        update={
            "max_pages": effective_max_pages,
            "max_concurrency": min(5, max(configured.max_concurrency, 3)),
            "max_discovered_urls": min(
                configured.max_discovered_urls, discovered_url_cap
            ),
            "max_sitemaps": min(
                configured.max_sitemaps,
                24 if range_days <= 31 and source_count >= 10 else 60,
            ),
            "max_sitemap_depth": min(configured.max_sitemap_depth, 3),
            # Separate deadlines leave time to prepare/persist completed pages.
            # These are coverage limits, not permission to relax date/SSRF gates.
            "probe_timeout_seconds": min(configured.probe_timeout_seconds, 10),
            "discovery_timeout_seconds": min(configured.discovery_timeout_seconds, 35 if source_count >= 10 else 50),
            "historical_timeout_seconds": min(configured.historical_timeout_seconds, 8 if source_count >= 10 else 12),
            "fetch_batch_timeout_seconds": min(configured.fetch_batch_timeout_seconds, 45 if source_count >= 10 else 75),
        }
    )
