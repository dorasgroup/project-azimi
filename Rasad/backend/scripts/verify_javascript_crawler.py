import asyncio

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy


async def verify_rendered_content() -> None:
    async with AsyncWebCrawler(
        config=BrowserConfig(
            headless=True,
            chrome_channel="chrome",
            verbose=False,
        )
    ) as crawler:
        result = await crawler.arun(
            url="https://quotes.toscrape.com/js/",
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                wait_until="networkidle",
                page_timeout=60_000,
            ),
        )

    assert result.success, result.error_message
    assert "The world as we have created it" in (result.html or ""), (
        "JavaScript-rendered quote was not found"
    )


async def verify_engine_browser_path() -> None:
    engine = CrawlEngine(
        CrawlPolicy(
            max_pages=1,
            max_concurrency=1,
            page_timeout_ms=60_000,
            render_javascript=True,
            use_stealth=True,
        )
    )
    result = await engine.crawl("https://www.python.org", "Python")

    assert result.probe.success, result.probe.reason
    assert result.discovered_links, "No topic-matching links discovered"
    assert any(page.success and page.content_hash for page in result.pages), (
        "CrawlEngine browser path did not crawl a page successfully"
    )


async def verify() -> None:
    await verify_rendered_content()
    await verify_engine_browser_path()
    print("JavaScript crawler verification passed")


if __name__ == "__main__":
    asyncio.run(verify())
