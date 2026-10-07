"""Smoke-test the live Common Crawl historical discovery adapter."""

import asyncio
from datetime import date

from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.infrastructure.common_crawl_discovery import (
    CommonCrawlDiscoveryProvider,
)


async def verify() -> None:
    policy = CrawlPolicy(
        max_external_results=120,
        common_crawl_max_indexes=1,
    )
    engine = CrawlEngine(policy)
    links = await CommonCrawlDiscoveryProvider(policy).discover(
        "https://blog.python.org",
        "Portuguese",
        engine._safe_get,
        date(2026, 6, 1),
        date(2026, 6, 30),
    )
    matching = [link for link in links if link.topic_matches]
    assert links, "Common Crawl returned no historical URLs"
    assert matching, "Common Crawl returned no topic-bearing historical URL"
    assert all(link.published_at is None for link in links)
    print(
        "Historical discovery verification passed: "
        f"{len(links)} URLs, {len(matching)} URL-topic matches"
    )


if __name__ == "__main__":
    asyncio.run(verify())
