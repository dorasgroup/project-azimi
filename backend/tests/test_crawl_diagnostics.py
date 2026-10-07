from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from app.crawler.schemas import CrawlBatchResult, CrawledPage, DiscoveredLink, SourceProbe
from app.models.enums import SourceCrawlStatus, SourceStatus
from app.modules.crawling.infrastructure.crawl_diagnostics import CrawlDiagnosticsBuilder
from app.modules.monitoring.infrastructure.crawl_health import SourceCrawlHealthUpdater


STARTED = datetime(2026, 3, 1, tzinfo=UTC)
FINISHED = STARTED + timedelta(seconds=2)
PROBE = SourceProbe(
    requested_url="https://example.com",
    success=True,
    http_status=200,
    robots_allowed=True,
)


def test_diagnostics_explains_empty_discovery() -> None:
    diagnostics = CrawlDiagnosticsBuilder.build(
        probe=PROBE,
        discovered=[],
        selected=[],
        pages=[],
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        max_pages=10,
        started_at=STARTED,
        finished_at=FINISHED,
    )

    assert diagnostics.status == "empty"
    assert diagnostics.reason_code == "no_urls_discovered"
    assert diagnostics.duration_ms == 2000


def test_diagnostics_counts_providers_limits_strategies_and_failures() -> None:
    links = [
        DiscoveredLink(
            url="https://example.com/a",
            discovered_via="rss",
            discovery_methods=["rss", "sitemap"],
        ),
        DiscoveredLink(
            url="https://example.com/b",
            discovered_via="gdelt",
            discovery_methods=["gdelt"],
        ),
        DiscoveredLink(
            url="https://example.com/old",
            discovered_via="rss",
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
        ),
    ]
    pages = [
        CrawledPage(
            url=links[0].url,
            success=True,
            status_code=200,
            metadata={"fetch_strategy": "http"},
        ),
        CrawledPage(
            url=links[1].url,
            success=False,
            status_code=503,
            metadata={"fetch_strategy": "browser", "failure_kind": "transient_http"},
        ),
    ]

    diagnostics = CrawlDiagnosticsBuilder.build(
        probe=PROBE,
        discovered=links,
        selected=links[:2],
        pages=pages,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        max_pages=2,
        started_at=STARTED,
        finished_at=FINISHED,
    )

    assert diagnostics.status == "partial"
    assert diagnostics.provider_counts == {"rss": 2, "sitemap": 1, "gdelt": 1}
    assert diagnostics.rejected_by_date == 1
    assert diagnostics.fetch_strategy_counts == {"http": 1, "browser": 1}
    assert diagnostics.failure_counts == {"transient_http": 1}
    assert diagnostics.http_status_counts == {"200": 1, "503": 1}


def test_source_health_separates_successful_connection_from_empty_crawl() -> None:
    diagnostics = CrawlDiagnosticsBuilder.build(
        probe=PROBE,
        discovered=[],
        selected=[],
        pages=[],
        date_from=None,
        date_to=None,
        max_pages=10,
        started_at=STARTED,
        finished_at=FINISHED,
    )
    result = CrawlBatchResult(
        source_url="https://example.com",
        probe=PROBE,
        discovered_links=[],
        pages=[],
        started_at=STARTED,
        finished_at=FINISHED,
        diagnostics=diagnostics,
    )
    source = SimpleNamespace()

    SourceCrawlHealthUpdater.update(source, result)

    assert source.last_status == SourceStatus.SUCCESS
    assert source.last_status_message is None
    assert source.last_crawl_status == SourceCrawlStatus.EMPTY
    assert source.last_crawl_message == diagnostics.summary
    assert source.last_crawled_at == FINISHED
