"""Smoke-test the phase-four HTTP-first and browser-fallback paths."""

import asyncio

from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy, DiscoveredLink


async def _fetch_one(url: str, *, render_javascript: bool) -> tuple[str, bool, int | None]:
    engine = CrawlEngine(
        CrawlPolicy(
            max_pages=1,
            max_concurrency=1,
            render_javascript=render_javascript,
            request_delay_ms=0,
            retry_count=0,
        )
    )
    pages = await engine._fetch_pages(
        url,
        [DiscoveredLink(url=url, discovered_via="verification")],
    )
    assert len(pages) == 1
    page = pages[0]
    assert page.success, page.error
    return str(page.metadata.get("fetch_strategy")), page.success, page.status_code


async def verify() -> None:
    static = await _fetch_one("https://www.python.org/", render_javascript=False)
    assert static[0] == "http", static

    rendered = await _fetch_one(
        "https://www.python.org/",
        render_javascript=True,
    )
    assert rendered[0] == "browser", rendered

    print(f"Resilient crawler verification passed: static={static}, rendered={rendered}")


if __name__ == "__main__":
    asyncio.run(verify())
