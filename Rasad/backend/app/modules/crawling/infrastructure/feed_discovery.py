"""RSS and Atom URL discovery."""

import asyncio

from app.crawler.discovery import discover_from_xml
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.http_transport import response_text
from app.modules.crawling.infrastructure.source_probe import HttpGet


class FeedDiscoveryProvider:
    """Fetch known feed endpoints and parse article records."""

    def __init__(self, policy: CrawlPolicy) -> None:
        self._policy = policy

    async def discover(
        self,
        source_url: str,
        topic: str,
        feed_urls: list[str],
        fetch: HttpGet,
    ) -> list[DiscoveredLink]:
        semaphore = asyncio.Semaphore(self._policy.max_concurrency)

        async def read_feed(url: str) -> list[DiscoveredLink]:
            try:
                async with semaphore:
                    response = await fetch(url)
                if response.status_code >= 400:
                    return []
                text = response_text(response)
                xml_start = text.lstrip()[:200].lower()
                content_type = response.headers.get("content-type", "").lower()
                if not (
                    "xml" in content_type
                    or "rss" in content_type
                    or xml_start.startswith("<?xml")
                    or xml_start.startswith("<rss")
                    or xml_start.startswith("<feed")
                ):
                    return []
                return discover_from_xml(
                    text,
                    source_url,
                    topic,
                    "rss",
                    self._policy.allow_subdomains,
                )
            except Exception:
                return []

        results = await asyncio.gather(*(read_feed(url) for url in feed_urls))
        return [link for links in results for link in links]
