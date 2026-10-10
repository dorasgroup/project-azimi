"""Smoke-test a configured source profile through the production crawler adapter."""

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
                "max_pages": 2,
                "max_concurrency": 2,
                "historical_discovery_enabled": False,
                "discovery_profile": {
                    "replace_default_seeds": True,
                    "search_templates": ["/search/?q={query}", "/?q={query}"],
                    "sitemap_paths": ["/sitemap.xml"],
                    "include_url_globs": ["https://www.python.org/*"],
                    "exclude_url_globs": ["*/about/*"],
                },
            },
        )
    )
    diagnostics = result.diagnostics
    assert diagnostics["successful_pages"] > 0, diagnostics
    assert diagnostics["provider_counts"].get("search", 0) > 0, diagnostics
    assert all("/about/" not in link.url for link in result.discovered_links)
    print(
        "Source profile verification passed: "
        f"selected={diagnostics['selected_total']}, providers={diagnostics['provider_counts']}"
    )


if __name__ == "__main__":
    asyncio.run(verify())
