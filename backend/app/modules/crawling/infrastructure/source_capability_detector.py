"""Bounded live capability checks for source-profile audits."""

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.domain.source_profiles import SourceCapabilitySnapshot
from app.modules.crawling.infrastructure.source_profile_registry import SourceStrategyRouter

HttpGet = Callable[..., Awaitable[httpx.Response]]


def _looks_like_xml(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "").casefold()
    prefix = response.text.lstrip()[:100].casefold()
    return response.status_code < 400 and (
        "xml" in content_type
        or "rss" in content_type
        or prefix.startswith(("<?xml", "<rss", "<feed", "<urlset", "<sitemapindex"))
    )


class SourceCapabilityDetector:
    """Observe base, robots, feed and sitemap reachability with a fixed budget."""

    def __init__(self, router: SourceStrategyRouter | None = None) -> None:
        self._router = router or SourceStrategyRouter()

    async def detect(
        self,
        source_url: str,
        fetch: HttpGet,
        policy: CrawlPolicy | None = None,
    ) -> SourceCapabilitySnapshot:
        plan = self._router.plan(source_url, policy or CrawlPolicy())
        endpoints = {
            "base": source_url,
            "robots": urljoin(source_url, "/robots.txt"),
        }
        endpoints.update(
            {
                f"feed_{index}": urljoin(source_url, path)
                for index, path in enumerate(
                    plan.policy.discovery_profile.feed_paths[:4], start=1
                )
            }
        )
        endpoints.update(
            {
                f"sitemap_{index}": urljoin(source_url, path)
                for index, path in enumerate(
                    plan.policy.discovery_profile.sitemap_paths[:4], start=1
                )
            }
        )

        async def request(name: str, url: str) -> tuple[str, httpx.Response | Exception]:
            try:
                return name, await fetch(url, max_redirects=5)
            except Exception as exc:
                return name, exc

        results = dict(await asyncio.gather(*(request(name, url) for name, url in endpoints.items())))
        base = results["base"]
        evidence = {
            name: (
                f"http_{value.status_code}:{value.url}"
                if isinstance(value, httpx.Response)
                else f"{type(value).__name__}:{value}"
            )
            for name, value in results.items()
        }
        base_ok = isinstance(base, httpx.Response) and base.status_code < 500
        final_url = str(base.url) if isinstance(base, httpx.Response) else None
        return SourceCapabilitySnapshot(
            profile_key=plan.profile.key,
            source_url=source_url,
            final_url=final_url,
            http_reachable=base_ok,
            http_status=base.status_code if isinstance(base, httpx.Response) else None,
            redirected=bool(final_url and final_url.rstrip("/") != source_url.rstrip("/")),
            robots_reachable=isinstance(results["robots"], httpx.Response) and results["robots"].status_code < 400,
            feed_reachable=any(
                name.startswith("feed_")
                and isinstance(value, httpx.Response)
                and _looks_like_xml(value)
                for name, value in results.items()
            ),
            sitemap_reachable=any(
                name.startswith("sitemap_")
                and isinstance(value, httpx.Response)
                and _looks_like_xml(value)
                for name, value in results.items()
            ),
            requires_browser=plan.profile.requires_browser,
            content_restricted=plan.profile.content_restricted,
            remote_egress_required=plan.profile.remote_egress_required,
            evidence=evidence,
        )
