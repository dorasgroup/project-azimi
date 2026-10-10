from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase

from app.crawler.schemas import CrawlPolicy, CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.infrastructure.crawl_orchestrator import CrawlOrchestrator


class CrawlOrchestratorTests(IsolatedAsyncioTestCase):
    async def test_stops_before_discovery_when_probe_is_not_usable(self) -> None:
        calls: list[str] = []

        async def probe(source_url: str) -> SourceProbe:
            calls.append(f"probe:{source_url}")
            return SourceProbe(requested_url=source_url, success=False, reason="offline")

        async def discover(*_args):
            calls.append("discover")
            return []

        async def fetch_pages(*_args):
            calls.append("fetch")
            return []

        result = await CrawlOrchestrator(CrawlPolicy()).crawl(
            "https://example.com",
            "topic",
            None,
            None,
            probe=probe,
            discover=discover,
            fetch_pages=fetch_pages,
        )

        self.assertEqual(calls, ["probe:https://example.com"])
        self.assertEqual(result.discovered_links, [])
        self.assertEqual(result.pages, [])
        self.assertEqual(result.diagnostics.reason_code, "probe_failed")

    async def test_selects_by_date_and_topic_before_fetching(self) -> None:
        fetched_urls: list[str] = []
        source_url = "https://example.com"

        async def probe(url: str) -> SourceProbe:
            return SourceProbe(
                requested_url=url,
                final_url="https://example.com/",
                success=True,
                robots_allowed=True,
            )

        async def discover(_url: str, _topic: str, _from: date | None, _to: date | None):
            return [
                DiscoveredLink(
                    url=f"{source_url}/outside",
                    discovered_via="rss",
                    published_at=datetime(2025, 12, 1, tzinfo=UTC),
                    topic_matches=["topic"],
                ),
                DiscoveredLink(
                    url=f"{source_url}/generic",
                    discovered_via="html",
                ),
                DiscoveredLink(
                    url=f"{source_url}/matching",
                    discovered_via="rss",
                    published_at=datetime(2026, 1, 15, tzinfo=UTC),
                    topic_matches=["topic"],
                ),
                DiscoveredLink(
                    url=f"{source_url}/dated",
                    discovered_via="rss",
                    published_at=datetime(2026, 1, 20, tzinfo=UTC),
                ),
            ]

        async def fetch_pages(_url: str, links: list[DiscoveredLink]):
            fetched_urls.extend(str(link.url) for link in links)
            return [CrawledPage(url=link.url, success=True) for link in links]

        result = await CrawlOrchestrator(CrawlPolicy(max_pages=2)).crawl(
            source_url,
            "topic",
            date(2026, 1, 1),
            date(2026, 1, 31),
            probe=probe,
            discover=discover,
            fetch_pages=fetch_pages,
        )

        self.assertEqual(
            fetched_urls,
            ["https://example.com/matching", "https://example.com/dated"],
        )
        self.assertEqual(len(result.pages), 2)
        self.assertEqual(str(result.probe.final_url), "https://example.com/")
        self.assertEqual(result.diagnostics.status, "success")
        self.assertEqual(result.diagnostics.discovered_total, 4)
        self.assertEqual(result.diagnostics.rejected_by_date, 1)
        self.assertEqual(result.diagnostics.skipped_by_limit, 1)
        self.assertEqual(result.diagnostics.selected_provider_counts, {"rss": 2})
