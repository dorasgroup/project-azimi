"""Backward-compatible facade for the separated crawler infrastructure."""

from datetime import date
import asyncio

import httpx
from crawl4ai import AsyncWebCrawler

from app.core.config import settings
from app.crawler.schemas import CrawlBatchResult, CrawlPolicy, CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.infrastructure.access_policy import RobotsAccessPolicy
from app.modules.crawling.infrastructure.crawl_orchestrator import CrawlOrchestrator
from app.modules.crawling.infrastructure.crawl_orchestrator import selection_trace
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy
from app.modules.crawling.infrastructure.http_transport import SafeHttpTransport
from app.modules.crawling.infrastructure.link_discovery import (
    LinkDiscoveryService,
    discovery_trace,
)
from app.modules.crawling.infrastructure.page_fetcher import Crawl4AiPageFetcher
from app.modules.crawling.infrastructure.source_probe import SourceProbeService
from app.modules.crawling.ports.checkpoint import CrawlCheckpointPort


def crawl_execution_trace(engine: "CrawlEngine") -> dict[str, object]:
    """Combine bounded discovery and selection traces for one completed crawl."""
    return {
        "discovery": discovery_trace(engine._discovery_service),
        "selection": selection_trace(engine._orchestrator),
    }


class CrawlEngine:
    """Preserve the legacy API while delegating each responsibility once."""

    def __init__(self, policy: object | None = None, *, checkpoint: CrawlCheckpointPort | None = None) -> None:
        if policy is not None and not isinstance(policy, CrawlPolicy):
            if hasattr(policy, "model_dump"):
                policy = CrawlPolicy.model_validate(policy.model_dump())
            elif isinstance(policy, dict):
                policy = CrawlPolicy.model_validate(policy)
        self.policy = policy or CrawlPolicy(
            max_pages=settings.crawler_max_pages_per_source,
            max_concurrency=settings.crawler_max_concurrency,
            page_timeout_ms=settings.crawler_page_timeout_ms,
            respect_robots_txt=settings.crawler_respect_robots_txt,
        )
        self.user_agent = settings.crawler_user_agent
        self._egress = CrawlEgressPolicy.from_environment()
        self._http = SafeHttpTransport(
            user_agent=self.user_agent,
            timeout_ms=self.policy.page_timeout_ms,
            egress=self._egress,
        )
        self._robots = RobotsAccessPolicy(self.policy, self.user_agent)
        self._probe_service = SourceProbeService()
        self._discovery_service = LinkDiscoveryService(self.policy)
        self._page_fetcher = Crawl4AiPageFetcher(
            self.policy,
            self.user_agent,
            self._egress,
            batch_timeout_seconds=self.policy.fetch_batch_timeout_seconds,
            checkpoint=checkpoint,
        )
        self._orchestrator = CrawlOrchestrator(self.policy, checkpoint)
        # Kept as the same object for existing diagnostics and verification scripts.
        self._robots_cache = self._robots.cache

    async def _safe_get(self, url: str, *, max_redirects: int = 5) -> httpx.Response:
        return await self._http.get(url, max_redirects=max_redirects)

    async def robots_allowed(self, url: str) -> bool:
        return await self._robots.is_allowed(url, self._safe_get)

    async def probe(self, url: str) -> SourceProbe:
        try:
            async with asyncio.timeout(self.policy.probe_timeout_seconds):
                return await self._probe_service.probe(url, self._http.probe, self.robots_allowed)
        except TimeoutError:
            return SourceProbe(requested_url=url, success=False, retryable=True,
                               reason="Source probe time budget exhausted")

    def _search_candidates(self, source_url: str, topic: str) -> list[tuple[str, str]]:
        return self._discovery_service.search_candidates(source_url, topic)

    @staticmethod
    def _prioritize_sitemaps(
        sitemap_urls: list[str],
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> list[str]:
        return LinkDiscoveryService.prioritize_sitemaps(
            sitemap_urls,
            date_from=date_from,
            date_to=date_to,
        )

    async def discover(
        self,
        source_url: str,
        topic: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DiscoveredLink]:
        return await self._discovery_service.discover(
            source_url,
            topic,
            self._safe_get,
            date_from,
            date_to,
        )

    async def _crawl_page(
        self,
        crawler: AsyncWebCrawler,
        source_url: str,
        url: str,
    ) -> CrawledPage:
        return await self._page_fetcher.fetch_page(
            crawler,
            source_url,
            url,
            self._safe_get,
            self.robots_allowed,
        )

    async def _fetch_pages(
        self,
        source_url: str,
        links: list[DiscoveredLink],
    ) -> list[CrawledPage]:
        return await self._page_fetcher.fetch_all(
            source_url,
            links,
            self._safe_get,
            self.robots_allowed,
        )

    async def crawl(
        self,
        source_url: str,
        topic: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> CrawlBatchResult:
        try:
            return await self._orchestrator.crawl(
                source_url, topic, date_from, date_to,
                probe=self.probe, discover=self.discover, fetch_pages=self._fetch_pages,
            )
        finally:
            await self._http._close()
