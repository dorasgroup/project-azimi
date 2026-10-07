import gzip
from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase, TestCase

import httpx

from app.crawler.discovery import (
    SitemapReference,
    canonicalize_url,
    discover_navigation_links,
    discover_sitemaps_from_robots,
)
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.discovery_merge import merge_discovered_links
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeed, DiscoverySeedBuilder
from app.modules.crawling.infrastructure.html_discovery import HtmlDiscoveryProvider
from app.modules.crawling.infrastructure.link_discovery import LinkDiscoveryService
from app.modules.crawling.infrastructure.sitemap_discovery import SitemapDiscoveryProvider


def _response(
    url: str,
    body: str | bytes,
    *,
    status: int = 200,
    content_type: str = "text/html",
) -> httpx.Response:
    content = body.encode() if isinstance(body, str) else body
    return httpx.Response(
        status,
        content=content,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


class DiscoveryParserTests(TestCase):
    def test_canonical_url_removes_tracking_and_sorts_content_query(self) -> None:
        self.assertEqual(
            canonicalize_url(
                "HTTPS://Example.COM/news?a=2&utm_source=x&b=1&fbclid=ignored#part"
            ),
            "https://example.com/news?a=2&b=1",
        )

    def test_robots_sitemaps_are_deduplicated_and_restricted_to_source(self) -> None:
        self.assertEqual(
            discover_sitemaps_from_robots(
                """
                User-agent: *
                Sitemap: /news-sitemap.xml
                Sitemap: https://example.com/news-sitemap.xml
                Sitemap: https://outside.example/sitemap.xml
                """,
                "https://example.com",
                False,
            ),
            ["https://example.com/news-sitemap.xml"],
        )

    def test_navigation_parser_finds_next_page_but_not_article_links(self) -> None:
        links = discover_navigation_links(
            """
            <a href="/news/one">article</a>
            <a class="pagination next" href="/search?q=x&page=2">بعدی</a>
            """,
            "https://example.com/search?q=x",
            False,
        )
        self.assertEqual(links, ["https://example.com/search?page=2&q=x"])

    def test_merge_preserves_all_discovery_methods_for_one_canonical_url(self) -> None:
        merged = merge_discovered_links(
            [
                [
                    DiscoveredLink(
                        url="https://example.com/news/1?utm_source=rss",
                        title="News",
                        discovered_via="rss",
                        discovery_methods=["rss"],
                    )
                ],
                [
                    DiscoveredLink(
                        url="https://example.com/news/1",
                        discovered_via="sitemap",
                        topic_matches=["topic"],
                        discovery_methods=["sitemap"],
                    )
                ],
            ],
            limit=100,
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].discovered_via, "rss")
        self.assertEqual(merged[0].discovery_methods, ["rss", "sitemap"])
        self.assertEqual(merged[0].topic_matches, ["topic"])

    def test_archive_seeds_cover_gregorian_and_jalali_target_months(self) -> None:
        seeds = DiscoverySeedBuilder(CrawlPolicy(max_archive_seeds=18)).archive_candidates(
            "https://news.ir",
            date(2026, 2, 28),
            date(2026, 4, 20),
        )
        urls = {url for url, via in seeds if via == "archive"}
        self.assertIn("https://news.ir/2026/02/", urls)
        self.assertIn("https://news.ir/2026/04/", urls)
        self.assertTrue(any("/1404/" in url for url in urls))
        self.assertTrue(any("/1405/" in url for url in urls))


class HybridDiscoveryAsyncTests(IsolatedAsyncioTestCase):
    async def test_html_search_follows_bounded_pagination(self) -> None:
        pages = {
            "https://example.com/search?q=topic": _response(
                "https://example.com/search?q=topic",
                """
                <a href="/news/one">Topic One</a>
                <a rel="next" href="/search?q=topic&page=2">Next</a>
                """,
            ),
            "https://example.com/search?page=2&q=topic": _response(
                "https://example.com/search?page=2&q=topic",
                '<a href="/news/two">Topic Two</a>',
            ),
        }

        async def fetch(url: str, **_kwargs) -> httpx.Response:
            return pages[url]

        links = await HtmlDiscoveryProvider(CrawlPolicy(max_discovery_pages=2)).discover(
            "https://example.com",
            "Topic",
            [DiscoverySeed("https://example.com/search?q=topic", "search")],
            fetch,
        )
        self.assertEqual(
            {str(link.url) for link in links},
            {"https://example.com/news/one", "https://example.com/news/two"},
        )
        self.assertTrue(all(link.discovered_via == "search" for link in links))

    async def test_recursive_sitemap_reads_more_than_twelve_children_and_gzip(self) -> None:
        root_url = "https://example.com/root.xml"
        children = [f"https://example.com/news-2026-{index:02d}.xml" for index in range(1, 16)]
        root_xml = "<sitemapindex>" + "".join(
            f"<sitemap><loc>{url}</loc></sitemap>" for url in children
        ) + "</sitemapindex>"
        pages: dict[str, httpx.Response] = {root_url: _response(root_url, root_xml, content_type="application/xml")}
        for index, child_url in enumerate(children, start=1):
            xml = (
                "<urlset><url>"
                f"<loc>https://example.com/news/topic-{index}</loc>"
                "<lastmod>2026-03-01T00:00:00Z</lastmod>"
                "</url></urlset>"
            )
            pages[child_url] = _response(
                child_url,
                gzip.compress(xml.encode()),
                content_type="application/gzip",
            )

        async def fetch(url: str, **_kwargs) -> httpx.Response:
            return pages[url]

        links = await SitemapDiscoveryProvider(
            CrawlPolicy(max_sitemaps=30, max_sitemap_depth=3, max_discovered_urls=100)
        ).discover(
            "https://example.com",
            "Topic",
            [root_url],
            fetch,
            date(2026, 2, 1),
            date(2026, 4, 30),
        )
        self.assertEqual(len(links), 15)
        self.assertEqual({link.discovered_via for link in links}, {"sitemap"})

    async def test_hybrid_service_merges_html_rss_and_robots_sitemap(self) -> None:
        source = "https://example.com"
        shared = "https://example.com/news/topic-story"
        responses = {
            f"{source}/robots.txt": _response(
                f"{source}/robots.txt",
                "Sitemap: https://example.com/custom-sitemap.xml",
                content_type="text/plain",
            ),
            f"{source}/": _response(
                f"{source}/",
                f'<a href="{shared}?utm_source=home">Topic Story</a>',
            ),
            f"{source}/feed": _response(
                f"{source}/feed",
                (
                    "<rss><channel><item>"
                    f"<link>{shared}</link><title>Topic Story</title>"
                    "<pubDate>Sun, 01 Mar 2026 00:00:00 GMT</pubDate>"
                    "</item></channel></rss>"
                ),
                content_type="application/rss+xml",
            ),
            f"{source}/custom-sitemap.xml": _response(
                f"{source}/custom-sitemap.xml",
                f"<urlset><url><loc>{shared}</loc></url></urlset>",
                content_type="application/xml",
            ),
        }

        async def fetch(url: str, **_kwargs) -> httpx.Response:
            return responses.get(url, _response(url, "not found", status=404))

        links = await LinkDiscoveryService(
            CrawlPolicy(max_archive_seeds=0, max_discovery_pages=1)
        ).discover(
            source,
            "Topic",
            fetch,
            date(2026, 2, 1),
            date(2026, 4, 30),
        )
        target = next(link for link in links if str(link.url) == shared)
        self.assertEqual(target.discovery_methods, ["rss", "sitemap", "html"])
        self.assertEqual(target.published_at, datetime(2026, 3, 1, tzinfo=UTC))

    async def test_sitemap_dated_links_outside_target_do_not_consume_results(self) -> None:
        sitemap_url = "https://example.com/sitemap.xml"
        xml = """
        <urlset>
          <url><loc>https://example.com/news/old</loc><lastmod>2025-01-01</lastmod></url>
          <url><loc>https://example.com/news/target</loc><lastmod>2026-03-10</lastmod></url>
          <url><loc>https://example.com/news/unknown</loc></url>
        </urlset>
        """

        async def fetch(url: str, **_kwargs) -> httpx.Response:
            return _response(url, xml, content_type="application/xml")

        links = await SitemapDiscoveryProvider(CrawlPolicy()).discover(
            "https://example.com",
            "Topic",
            [sitemap_url],
            fetch,
            date(2026, 3, 1),
            date(2026, 3, 31),
        )
        self.assertEqual(
            {str(link.url) for link in links},
            {"https://example.com/news/target", "https://example.com/news/unknown"},
        )

    def test_sitemap_priority_prefers_target_month_and_year(self) -> None:
        ordered = SitemapDiscoveryProvider.prioritize_references(
            [
                SitemapReference("https://example.com/news-2024.xml"),
                SitemapReference("https://example.com/news-2026.xml"),
                SitemapReference("https://example.com/news-2026-03.xml"),
                SitemapReference("https://example.com/news-1404-12.xml"),
            ],
            date_from=date(2026, 3, 1),
            date_to=date(2026, 3, 31),
        )
        self.assertEqual(
            {reference.url for reference in ordered[:2]},
            {
                "https://example.com/news-2026-03.xml",
                "https://example.com/news-1404-12.xml",
            },
        )
    async def test_sitemap_initial_budget_prefers_target_period_seed(self) -> None:
        requested: list[str] = []
        target = "https://example.com/sitemap/1404/sitemap.xml"
        current = "https://example.com/sitemap/1405/sitemap.xml"

        async def fetch(url: str, **_kwargs) -> httpx.Response:
            requested.append(url)
            return _response(url, "<urlset />", content_type="application/xml")

        await SitemapDiscoveryProvider(CrawlPolicy(max_sitemaps=1)).discover(
            "https://example.com",
            "Topic",
            [current, target],
            fetch,
            date(2026, 2, 28),
            date(2026, 4, 9),
        )

        self.assertEqual(requested, [target])
