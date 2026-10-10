"""HTML, internal-search and archive discovery with bounded pagination."""

import asyncio
from collections import deque

from app.crawler.discovery import discover_from_html, discover_navigation_links
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeed
from app.modules.crawling.infrastructure.http_transport import has_usable_html, response_text
from app.modules.crawling.infrastructure.source_probe import HttpGet


class HtmlDiscoveryProvider:
    """Read HTML discovery seeds and follow only bounded pagination links."""

    def __init__(self, policy: CrawlPolicy) -> None:
        self._policy = policy

    async def discover(
        self,
        source_url: str,
        topic: str,
        seeds: list[DiscoverySeed],
        fetch: HttpGet,
    ) -> list[DiscoveredLink]:
        semaphore = asyncio.Semaphore(self._policy.max_concurrency)

        async def crawl_seed(seed: DiscoverySeed) -> list[DiscoveredLink]:
            queue = deque([seed.url])
            visited: set[str] = set()
            links: list[DiscoveredLink] = []
            while queue and len(visited) < self._policy.max_discovery_pages:
                page_url = queue.popleft()
                if page_url in visited:
                    continue
                visited.add(page_url)
                try:
                    async with semaphore:
                        response = await fetch(page_url)
                    text = response_text(response)
                    if response.status_code >= 400 and not has_usable_html(
                        response.status_code,
                        response.headers.get("content-type"),
                        text,
                    ):
                        continue
                    links.extend(
                        discover_from_html(
                            text,
                            str(response.url),
                            topic,
                            self._policy.allow_subdomains,
                            via=seed.via,
                        )
                    )
                    if seed.via in {"search", "archive"}:
                        for next_url in discover_navigation_links(
                            text,
                            str(response.url),
                            self._policy.allow_subdomains,
                        ):
                            if next_url not in visited:
                                queue.append(next_url)
                except Exception:
                    continue
            return links

        results = await asyncio.gather(*(crawl_seed(seed) for seed in seeds))
        return [link for links in results for link in links]
