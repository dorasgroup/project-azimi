import asyncio

from app.crawler.discovery import discover_from_html
from app.crawler.engine import CrawlEngine
from app.crawler.schemas import CrawlPolicy
from app.crawler.security import UnsafeUrlError, validate_public_url


async def verify() -> None:
    try:
        await validate_public_url("http://127.0.0.1/private")
    except UnsafeUrlError:
        pass
    else:
        raise AssertionError("Loopback SSRF URL was not rejected")

    links = discover_from_html(
        """
        <a href="/news/iran-topic">Iran Topic News</a>
        <a href="/news/general-story">General Story</a>
        <a href="/about">Iran company information</a>
        <a href="https://outside.example/iran">External Iran</a>
        """,
        "https://example.com",
        "Iran",
        allow_subdomains=False,
    )
    assert [str(link.url) for link in links] == [
        "https://example.com/news/iran-topic",
        "https://example.com/news/general-story",
    ]
    assert links[0].topic_matches == ["iran"]
    assert links[1].topic_matches == []

    engine = CrawlEngine(CrawlPolicy(max_pages=3, max_concurrency=2, render_javascript=False))
    probe = await engine.probe("https://www.python.org")
    assert probe.success, probe.reason
    result = await engine.crawl("https://www.python.org", "Python")
    assert result.probe.success
    assert result.discovered_links, "No topic-matching links discovered"
    assert any(page.success and page.content_hash for page in result.pages), (
        "No page was crawled successfully"
    )
    assert all(
        str(link.url).startswith("https://www.python.org/") for link in result.discovered_links
    )

    print(
        "Crawler verification passed: "
        f"{len(result.discovered_links)} links discovered, "
        f"{sum(page.success for page in result.pages)} pages crawled"
    )


if __name__ == "__main__":
    asyncio.run(verify())
