import json
from datetime import UTC, date, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.common_crawl_discovery import (
    COMMON_CRAWL_COLLECTIONS,
    CommonCrawlDiscoveryProvider,
)
from app.modules.crawling.infrastructure.discovery_merge import merge_discovered_links
from app.modules.crawling.infrastructure.gdelt_discovery import GdeltDiscoveryProvider
from app.modules.crawling.infrastructure.historical_discovery import HistoricalDiscoveryService
from app.modules.crawling.infrastructure.link_discovery import LinkDiscoveryService


SOURCE = "https://news.example.com"


def test_historical_sufficiency_counts_only_topical_dated_local_links() -> None:
    links = [
        DiscoveredLink(
            url="https://news.example.com/current-shell",
            discovered_via="sitemap",
            topic_matches=["iran"],
        ),
        DiscoveredLink(
            url="https://news.example.com/generic-target-date",
            discovered_via="sitemap",
            published_at=datetime(2026, 3, 10, tzinfo=UTC),
        ),
        DiscoveredLink(
            url="https://news.example.com/topical-target-date",
            discovered_via="rss",
            topic_matches=["women", "iran"],
            published_at=datetime(2026, 3, 12, tzinfo=UTC),
        ),
        DiscoveredLink(
            url="https://news.example.com/topical-old",
            discovered_via="rss",
            topic_matches=["women", "iran"],
            published_at=datetime(2025, 3, 12, tzinfo=UTC),
        ),
    ]

    assert LinkDiscoveryService._historical_eligible_count(
        links,
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
    ) == 1


def test_historical_candidates_are_not_starved_by_a_full_local_budget() -> None:
    historical = DiscoveredLink(
        url="https://news.example.com/archive/women-iran",
        discovered_via="common_crawl",
        topic_matches=["women", "iran"],
    )
    local = [
        DiscoveredLink(
            url=f"https://news.example.com/current/{index}",
            discovered_via="sitemap",
        )
        for index in range(3)
    ]

    merged = merge_discovered_links([[historical], local], limit=3)

    assert historical.url in {link.url for link in merged}


def _response(url: str, body: str, content_type: str = "application/json") -> httpx.Response:
    return httpx.Response(
        200,
        text=body,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


@pytest.mark.asyncio
async def test_gdelt_bounds_query_and_keeps_only_source_urls() -> None:
    requested: list[str] = []
    payload = {
        "articles": [
            {
                "url": "https://news.example.com/world/iran-talks",
                "title": "Iran talks resume",
                "seendate": "20260310T120000Z",
            },
            {
                "url": "https://outside.example/world/iran-talks",
                "title": "Outside result",
                "seendate": "20260310T120000Z",
            },
            {
                "url": "https://news.example.com/world/old-iran-talks",
                "title": "Old result",
                "seendate": "20250110T120000Z",
            },
        ]
    }

    async def fetch(url: str, **_kwargs: object) -> httpx.Response:
        requested.append(url)
        return _response(url, json.dumps(payload))

    links = await GdeltDiscoveryProvider(CrawlPolicy()).discover(
        SOURCE,
        "Iran talks",
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )

    assert [str(link.url) for link in links] == [
        "https://news.example.com/world/iran-talks"
    ]
    assert links[0].discovery_methods == ["gdelt"]
    assert links[0].published_at is None
    queries = [parse_qs(urlparse(url).query) for url in requested]
    assert [
        (query["startdatetime"][0], query["enddatetime"][0]) for query in queries
    ] == [
        ("20260322000000", "20260331235959"),
        ("20260312000000", "20260321235959"),
        ("20260301000000", "20260311235959"),
    ]
    assert all("domain:news.example.com" in query["query"][0] for query in queries)
    assert all(query["maxrecords"] == ["40"] for query in queries)


@pytest.mark.asyncio
async def test_gdelt_uses_source_language_query_plan_without_replacing_topic() -> None:
    requested: list[str] = []

    async def fetch(url: str, **_kwargs: object) -> httpx.Response:
        requested.append(url)
        return _response(url, json.dumps({"articles": []}))

    topic = "بررسی مسئله زن در ایران"
    await GdeltDiscoveryProvider(CrawlPolicy(), ("en",)).discover(
        SOURCE,
        topic,
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    generated = parse_qs(urlparse(requested[0]).query)["query"][0]

    assert '"women iran"' in generated
    assert topic not in generated
    assert "domain:news.example.com" in generated


@pytest.mark.asyncio
async def test_gdelt_adds_english_retrieval_for_persian_source() -> None:
    requested: list[str] = []

    async def fetch(url: str, **_kwargs: object) -> httpx.Response:
        requested.append(url)
        return _response(url, json.dumps({"articles": []}))

    topic = "بررسی رویکرد رسانه های داخلی و خارجی به مسئله زن در ایران"
    await GdeltDiscoveryProvider(CrawlPolicy(), ("fa",)).discover(
        SOURCE,
        topic,
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 10),
    )
    generated = parse_qs(urlparse(requested[0]).query)["query"][0]

    assert '"women iran"' in generated
    assert topic in generated
    assert "domain:news.example.com" in generated


@pytest.mark.asyncio
async def test_common_crawl_selects_overlapping_collections_and_parses_cdx() -> None:
    requested: list[str] = []
    collections = [
        {
            "id": "CC-MAIN-2026-12",
            "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-12-index",
            "from": "2026-03-01T00:00:00Z",
            "to": "2026-03-15T23:59:59Z",
        },
        {
            "id": "CC-MAIN-2025-01",
            "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2025-01-index",
            "from": "2025-01-01T00:00:00Z",
            "to": "2025-01-15T23:59:59Z",
        },
    ]
    records = "\n".join(
        json.dumps(record)
        for record in [
            {
                "url": "https://news.example.com/world/iran-analysis",
                "timestamp": "20260310110000",
                "status": "200",
                "mime": "text/html",
            },
            {
                "url": "https://outside.example/world/iran-analysis",
                "timestamp": "20260310110000",
                "status": "200",
                "mime": "text/html",
            },
        ]
    )

    async def fetch(url: str, **_kwargs: object) -> httpx.Response:
        requested.append(url)
        if url == COMMON_CRAWL_COLLECTIONS:
            return _response(url, json.dumps(collections))
        return _response(url, records, "application/x-ndjson")

    links = await CommonCrawlDiscoveryProvider(
        CrawlPolicy(common_crawl_max_indexes=2, max_external_results=20)
    ).discover(
        SOURCE,
        "Iran",
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )

    assert [str(link.url) for link in links] == [
        "https://news.example.com/world/iran-analysis"
    ]
    assert links[0].discovered_via == "common_crawl"
    assert any("CC-MAIN-2026-12-index" in url for url in requested)
    assert not any("CC-MAIN-2025-01-index" in url for url in requested)


@pytest.mark.asyncio
async def test_common_crawl_samples_pages_across_large_cdx_index() -> None:
    requested_pages: list[int] = []
    collections = [
        {
            "id": "CC-MAIN-2026-12",
            "cdx-api": "https://index.commoncrawl.org/CC-MAIN-2026-12-index",
            "from": "2026-03-01T00:00:00Z",
            "to": "2026-03-31T23:59:59Z",
        }
    ]

    async def fetch(url: str, **_kwargs: object) -> httpx.Response:
        if url == COMMON_CRAWL_COLLECTIONS:
            return _response(url, json.dumps(collections))
        query = parse_qs(urlparse(url).query)
        if query.get("showNumPages") == ["true"]:
            return _response(url, json.dumps({"pages": 9, "pageSize": 1}))
        page = int(query["page"][0])
        requested_pages.append(page)
        suffix = "women-iran" if page in {4, 8} else "generic-index"
        return _response(
            url,
            json.dumps(
                {
                    "url": f"https://news.example.com/world/{suffix}-{page}",
                    "timestamp": "20260310110000",
                    "status": "200",
                    "mime": "text/html",
                }
            ),
            "application/x-ndjson",
        )

    links = await CommonCrawlDiscoveryProvider(CrawlPolicy()).discover(
        SOURCE,
        "women iran",
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )

    assert requested_pages == [0, 4, 8]
    assert {
        str(link.url) for link in links if link.topic_matches
    } == {
        "https://news.example.com/world/women-iran-4",
        "https://news.example.com/world/women-iran-8",
    }


@pytest.mark.asyncio
async def test_historical_fallback_is_skipped_when_local_discovery_is_sufficient() -> None:
    policy = CrawlPolicy(historical_fallback_min_results=3)
    service = HistoricalDiscoveryService(policy)

    async def unexpected_fetch(_url: str, **_kwargs: object) -> httpx.Response:
        raise AssertionError("external provider should not be called")

    links = await service.discover(
        SOURCE,
        "Iran",
        unexpected_fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
        existing_count=3,
    )

    assert links == []


@pytest.mark.asyncio
async def test_historical_fallback_calls_enabled_providers_only_below_threshold() -> None:
    policy = CrawlPolicy(historical_fallback_min_results=3)
    service = HistoricalDiscoveryService(policy)
    calls: list[str] = []

    async def gdelt_discover(*_args: object) -> list[DiscoveredLink]:
        calls.append("gdelt")
        return [
            DiscoveredLink(
                url="https://news.example.com/archive/gdelt",
                discovered_via="gdelt",
            )
        ]

    async def common_crawl_discover(*_args: object) -> list[DiscoveredLink]:
        calls.append("common_crawl")
        return [
            DiscoveredLink(
                url="https://news.example.com/archive/common-crawl",
                discovered_via="common_crawl",
            )
        ]

    service._gdelt.discover = gdelt_discover  # type: ignore[method-assign]
    service._common_crawl.discover = common_crawl_discover  # type: ignore[method-assign]

    links = await service.discover(
        SOURCE,
        "Iran",
        lambda *_args, **_kwargs: None,  # type: ignore[arg-type,return-value]
        date(2026, 3, 1),
        date(2026, 3, 31),
        existing_count=2,
    )

    assert calls == ["gdelt", "common_crawl"]
    assert {link.discovered_via for link in links} == {"gdelt", "common_crawl"}


@pytest.mark.asyncio
async def test_historical_fallback_is_skipped_without_complete_date_window() -> None:
    service = HistoricalDiscoveryService(CrawlPolicy(historical_fallback_min_results=3))

    async def unexpected_fetch(_url: str, **_kwargs: object) -> httpx.Response:
        raise AssertionError("external provider should not be called")

    links = await service.discover(
        SOURCE,
        "Iran",
        unexpected_fetch,
        date(2026, 3, 1),
        None,
        existing_count=0,
    )

    assert links == []


@pytest.mark.asyncio
async def test_historical_provider_failure_does_not_fail_other_provider() -> None:
    policy = CrawlPolicy(
        gdelt_discovery_enabled=True,
        common_crawl_discovery_enabled=False,
        historical_fallback_min_results=1,
    )
    service = HistoricalDiscoveryService(policy)

    async def failing_fetch(_url: str, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("unavailable")

    links = await service.discover(
        SOURCE,
        "Iran",
        failing_fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
        existing_count=0,
    )

    assert links == []
