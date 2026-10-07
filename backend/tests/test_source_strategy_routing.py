from datetime import date

import httpx
import pytest

from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.infrastructure.link_discovery import (
    LinkDiscoveryService,
    discovery_trace,
)


def _response(url: str, body: str = "not found", status: int = 404) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        request=httpx.Request("GET", url),
        headers={"content-type": "text/plain"},
    )


@pytest.mark.asyncio
async def test_strategy_trace_explains_attempted_skipped_and_fallback_providers() -> None:
    requested: list[str] = []

    async def fetch(url: str, **_kwargs) -> httpx.Response:
        requested.append(url)
        return _response(url)

    service = LinkDiscoveryService(
        CrawlPolicy(max_archive_seeds=0, max_discovery_pages=1, max_external_results=1)
    )
    await service.discover(
        "https://www.economist.com",
        "Iran",
        fetch,
        date(2026, 3, 1),
        date(2026, 3, 31),
    )
    trace = discovery_trace(service)

    assert trace["profile_key"] == "economist"
    assert trace["attempted"][:3] == ["rss", "html", "sitemap"]
    assert trace["fallbacks_used"] == ["historical_discovery"]
    assert trace["skipped"]["archive"] == "not declared by source profile"
    assert trace["recommended_action"] == "validate source endpoints and server egress"
    assert trace["query_languages"] == ["en"]
    assert trace["retrieval_queries"] == ["Iran"]
    assert all(url.startswith(("https://www.economist.com", "https://api.gdeltproject.org", "https://index.commoncrawl.org")) for url in requested)


@pytest.mark.asyncio
async def test_unknown_source_keeps_safe_generic_hybrid_discovery() -> None:
    async def fetch(url: str, **_kwargs) -> httpx.Response:
        if url == "https://unknown-news.example/feed":
            return httpx.Response(
                200,
                text=(
                    "<rss><channel><item><link>https://unknown-news.example/news/1</link>"
                    "<title>Topic</title></item></channel></rss>"
                ),
                request=httpx.Request("GET", url),
                headers={"content-type": "application/rss+xml"},
            )
        return _response(url)

    service = LinkDiscoveryService(CrawlPolicy(max_archive_seeds=0, historical_discovery_enabled=False))
    links = await service.discover("https://unknown-news.example", "Topic", fetch)

    assert [str(link.url) for link in links] == ["https://unknown-news.example/news/1"]
    assert discovery_trace(service)["profile_key"] == "generic"
