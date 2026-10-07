"""Smoke-test crawl diagnostics on a live source."""

import asyncio

from app.modules.crawling.domain import CrawlRequest
from app.modules.crawling.infrastructure import Crawl4AiSourceCrawler


async def verify() -> None:
    result = await Crawl4AiSourceCrawler().crawl(
        CrawlRequest(
            source_url="https://www.python.org",
            topic="Python",
            date_from=None,
            date_to=None,
            policy={
                "max_pages": 3,
                "max_concurrency": 2,
                "historical_discovery_enabled": False,
            },
        )
    )
    diagnostics = result.diagnostics
    assert diagnostics["reason_code"] == "completed", diagnostics
    assert diagnostics["discovered_total"] >= diagnostics["selected_total"] > 0
    assert diagnostics["successful_pages"] > 0
    assert diagnostics["provider_counts"]
    assert diagnostics["fetch_strategy_counts"]
    print(
        "Crawl diagnostics verification passed: "
        f"status={diagnostics['status']}, providers={diagnostics['provider_counts']}, "
        f"fetch={diagnostics['fetch_strategy_counts']}"
    )


if __name__ == "__main__":
    asyncio.run(verify())
