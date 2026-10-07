"""Coordinate optional external historical discovery providers."""

import asyncio
from datetime import date

from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.infrastructure.common_crawl_discovery import (
    CommonCrawlDiscoveryProvider,
)
from app.modules.crawling.infrastructure.gdelt_discovery import GdeltDiscoveryProvider
from app.modules.crawling.infrastructure.source_probe import HttpGet


class HistoricalDiscoveryService:
    """Run enabled historical providers without allowing one failure to stop discovery."""

    def __init__(
        self,
        policy: CrawlPolicy,
        languages: tuple[str, ...] | None = None,
    ) -> None:
        self._policy = policy
        self._gdelt = GdeltDiscoveryProvider(policy, languages)
        self._common_crawl = CommonCrawlDiscoveryProvider(policy)

    async def discover(
        self,
        source_url: str,
        topic: str,
        fetch: HttpGet,
        date_from: date | None,
        date_to: date | None,
        existing_count: int,
    ) -> list[DiscoveredLink]:
        if (
            not self._policy.historical_discovery_enabled
            or not date_from
            or not date_to
            or existing_count >= self._policy.historical_fallback_min_results
        ):
            return []
        tasks = []
        if self._policy.gdelt_discovery_enabled:
            tasks.append(self._gdelt.discover(source_url, topic, fetch, date_from, date_to))
        if self._policy.common_crawl_discovery_enabled:
            tasks.append(
                self._common_crawl.discover(source_url, topic, fetch, date_from, date_to)
            )
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            link
            for result in results
            if isinstance(result, list)
            for link in result
        ]
