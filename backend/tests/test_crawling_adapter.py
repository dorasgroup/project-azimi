from unittest import TestCase

import pytest

pytest.importorskip("crawl4ai")

from app.crawler.schemas import CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.infrastructure.crawl4ai_adapter import (
    _link_record,
    _page_record,
    _probe_result,
)


class Crawl4AiBoundaryMappingTests(TestCase):
    def test_legacy_crawler_models_are_mapped_to_domain_records(self) -> None:
        probe = _probe_result(
            SourceProbe(
                requested_url="https://example.com",
                final_url="https://example.com/news",
                success=True,
                http_status=200,
                response_time_ms=17,
                content_type="text/html",
                redirected=True,
                robots_allowed=True,
            )
        )
        link = _link_record(
            DiscoveredLink(
                url="https://example.com/a",
                title="A",
                discovered_via="rss",
                topic_matches=["topic"],
                discovery_methods=["rss", "sitemap"],
            )
        )
        page = _page_record(
            CrawledPage(
                url="https://example.com/a",
                success=False,
                error="timeout",
                metadata={"attempt": 2},
            )
        )

        self.assertEqual(probe.response_time_ms, 17)
        self.assertEqual(link.topic_matches, ("topic",))
        self.assertEqual(link.discovery_methods, ("rss", "sitemap"))
        self.assertEqual(page.metadata, {"attempt": 2})
        self.assertEqual(page.error, "timeout")
