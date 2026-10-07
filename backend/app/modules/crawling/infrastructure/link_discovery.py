"""Hybrid URL discovery facade for a single news source."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date

import httpx

from app.crawler.discovery import SitemapReference
from app.crawler.schemas import CrawlPolicy, DiscoveredLink
from app.modules.crawling.domain.source_profiles import DiscoveryMethod, DiscoveryStrategyTrace
from app.modules.crawling.infrastructure.discovery_merge import merge_discovered_links
from app.modules.crawling.infrastructure.discovery_seeds import DiscoverySeedBuilder
from app.modules.crawling.infrastructure.feed_discovery import FeedDiscoveryProvider
from app.modules.crawling.infrastructure.html_discovery import HtmlDiscoveryProvider
from app.modules.crawling.infrastructure.historical_discovery import HistoricalDiscoveryService
from app.modules.crawling.infrastructure.sitemap_discovery import SitemapDiscoveryProvider
from app.modules.crawling.infrastructure.source_profile_registry import SourceStrategyRouter
from app.modules.crawling.infrastructure.url_admission import SourceUrlAdmissionPolicy
from app.processing.query_planner import query_plan


HttpGet = Callable[..., Awaitable[httpx.Response]]
logger = logging.getLogger(__name__)


def discovery_trace(service: "LinkDiscoveryService") -> dict[str, object]:
    """Return a serialization-safe copy of the latest strategy trace."""
    return service._last_trace.as_dict()


class LinkDiscoveryService:
    """Combine independent HTML, feed and recursive-sitemap discovery providers."""

    def __init__(self, policy: CrawlPolicy) -> None:
        self._policy = policy
        self._router = SourceStrategyRouter()
        self._last_trace = DiscoveryStrategyTrace(profile_key="not_resolved")

    @staticmethod
    def search_candidates(source_url: str, topic: str) -> list[tuple[str, str]]:
        return DiscoverySeedBuilder.search_candidates(source_url, topic)

    @staticmethod
    def prioritize_sitemaps(
        sitemap_urls: list[str],
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> list[str]:
        references = [SitemapReference(url=url) for url in sitemap_urls]
        return [
            reference.url
            for reference in SitemapDiscoveryProvider.prioritize_references(
                references,
                date_from=date_from,
                date_to=date_to,
            )
        ]

    @staticmethod
    def _historical_eligible_count(
        links: list[DiscoveredLink],
        *,
        date_from: date | None,
        date_to: date | None,
    ) -> int:
        """Count locally discovered links that can actually satisfy a dated crawl."""
        if not date_from or not date_to:
            return len(links)
        return sum(
            bool(link.topic_matches)
            and link.published_at is not None
            and date_from <= link.published_at.date() <= date_to
            for link in links
        )

    async def discover(
        self,
        source_url: str,
        topic: str,
        fetch: HttpGet,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DiscoveredLink]:
        plan = self._router.plan(source_url, self._policy)
        policy = plan.policy
        topic_plan = query_plan(topic, plan.profile.languages)
        seeds = DiscoverySeedBuilder(
            policy,
            plan.profile.languages,
            search_enabled=DiscoveryMethod.SEARCH in plan.enabled,
            archive_enabled=DiscoveryMethod.ARCHIVE in plan.enabled,
        )
        html = HtmlDiscoveryProvider(policy)
        feeds = FeedDiscoveryProvider(policy)
        sitemaps = SitemapDiscoveryProvider(policy)
        admission = SourceUrlAdmissionPolicy(policy.discovery_profile)
        trace = DiscoveryStrategyTrace(
            plan.profile.key,
            skipped=plan.skipped.copy(),
            query_languages=list(topic_plan.target_languages),
            retrieval_queries=list(topic_plan.queries),
        )
        # Bound HTTP at the call boundary, not around an entire provider: a
        # provider can return its already discovered links when its tail stalls.
        # Cancellation from the parent still propagates normally.
        local_deadline = asyncio.get_running_loop().time() + policy.discovery_timeout_seconds
        exhausted = set()

        def bounded_fetch(deadline, stage):
            async def get(url, **kwargs):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    exhausted.add(stage)
                    raise TimeoutError("Discovery time budget exhausted")
                try:
                    async with asyncio.timeout(remaining):
                        return await fetch(url, **kwargs)
                except TimeoutError:
                    exhausted.add(stage)
                    raise
            return get

        local_fetch = bounded_fetch(local_deadline, "local")
        robots_sitemaps = (
            await sitemaps.robots_sitemaps(source_url, local_fetch)
            if DiscoveryMethod.SITEMAP in plan.enabled
            else []
        )
        sitemap_seeds = list(
            dict.fromkeys(
                [
                    *robots_sitemaps,
                    *seeds.default_sitemap_urls(source_url),
                ]
            )
        )
        tasks: dict[str, Awaitable[list[DiscoveredLink]]] = {}
        if DiscoveryMethod.FEED in plan.enabled:
            tasks["rss"] = feeds.discover(source_url, topic, seeds.feed_urls(source_url), local_fetch)
        if any(method in plan.enabled for method in (DiscoveryMethod.HTML, DiscoveryMethod.SEARCH, DiscoveryMethod.ARCHIVE)):
            tasks["html"] = html.discover(
                source_url,
                topic,
                seeds.html_seeds(source_url, topic, date_from, date_to),
                local_fetch,
            )
        if DiscoveryMethod.SITEMAP in plan.enabled:
            tasks["sitemap"] = sitemaps.discover(
                source_url, topic, sitemap_seeds, local_fetch, date_from, date_to
            )
        provider_results = await asyncio.gather(*tasks.values())
        local_groups = []
        for provider, links in zip(tasks, provider_results, strict=True):
            trace.attempted.append(provider)
            trace.provider_counts[provider] = len(links)
            local_groups.append(admission.filter(links))
        local_links = merge_discovered_links(
            local_groups,
            limit=policy.max_discovered_urls,
        )
        eligible_count = self._historical_eligible_count(
            local_links, date_from=date_from, date_to=date_to
        )
        historical_enabled = policy.historical_discovery_enabled and bool(date_from and date_to)
        fallback_needed = historical_enabled and eligible_count < policy.historical_fallback_min_results
        historical_links = await HistoricalDiscoveryService(
            policy, plan.profile.languages
        ).discover(
            source_url,
            topic,
            bounded_fetch(asyncio.get_running_loop().time() + policy.historical_timeout_seconds, "historical"),
            date_from,
            date_to,
            eligible_count,
        )
        if fallback_needed:
            trace.fallbacks_used.append("historical_discovery")
            for method in (DiscoveryMethod.GDELT, DiscoveryMethod.COMMON_CRAWL):
                if method in plan.enabled:
                    trace.attempted.append(method.value)
        for link in historical_links:
            provider = link.discovered_via
            trace.provider_counts[provider] = trace.provider_counts.get(provider, 0) + 1
        result = merge_discovered_links(
            [admission.filter(historical_links), local_links],
            limit=policy.max_discovered_urls,
        )
        if not result:
            trace.recommended_action = (
                "validate source endpoints and server egress"
                if plan.profile.remote_egress_required
                else "review source profile endpoints"
            )
        self._last_trace = trace
        if exhausted:
            trace.skipped["time_budget"] = ",".join(sorted(exhausted))
            trace.recommended_action = "Partial discovery coverage: stage deadline reached"
        logger.info("crawl_discovery_strategy", extra={"strategy_trace": trace.as_dict()})
        return result
