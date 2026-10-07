"""Browser-only rendering fallback for JavaScript-dependent pages."""

import hashlib
from collections.abc import Awaitable, Callable

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

from app.crawler.schemas import CrawlPolicy, CrawledPage
from app.crawler.security import UnsafeUrlError, host_is_allowed
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy
from app.modules.crawling.infrastructure.http_page_fetcher import _visible_text_length


RobotsCheck = Callable[[str], Awaitable[bool]]


class BrowserPageRenderer:
    """Create a browser and render one page after an HTTP-stage fallback decision."""

    def __init__(
        self,
        policy: CrawlPolicy,
        user_agent: str,
        egress: CrawlEgressPolicy | None = None,
    ) -> None:
        self._policy = policy
        self._user_agent = user_agent
        self._egress = egress or CrawlEgressPolicy()

    def create_crawler(self) -> AsyncWebCrawler:
        return AsyncWebCrawler(
            config=BrowserConfig(
                headless=True,
                enable_stealth=self._policy.use_stealth,
                user_agent=self._user_agent,
                chrome_channel="chrome",
                verbose=False,
                proxy=self._egress.proxy_url,
            )
        )

    async def render(
        self,
        crawler: AsyncWebCrawler,
        source_url: str,
        url: str,
        robots_check: RobotsCheck,
        fallback_reason: str,
    ) -> CrawledPage:
        try:
            await self._egress.validate(url)
            if not host_is_allowed(url, source_url, self._policy.allow_subdomains):
                raise UnsafeUrlError("URL is outside the allowed source host")
            if not await robots_check(url):
                raise PermissionError("Blocked by robots.txt")
            result = await crawler.arun(
                url=url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    check_robots_txt=self._policy.respect_robots_txt,
                    page_timeout=self._policy.browser_timeout_ms,
                    max_retries=self._policy.retry_count,
                    wait_until="domcontentloaded",
                    delay_before_return_html=1.0,
                    remove_overlay_elements=True,
                    remove_consent_popups=True,
                    exclude_external_links=True,
                    user_agent=self._user_agent,
                ),
            )
            final_url = result.redirected_url or result.url
            await self._egress.validate(final_url)
            if not host_is_allowed(final_url, source_url, self._policy.allow_subdomains):
                raise UnsafeUrlError("Redirected outside the allowed source host")
            html = result.html or ""
            metadata = result.metadata or {}
            markdown = result.markdown.raw_markdown if result.markdown else ""
            visible_length = _visible_text_length(html)
            rendered_content_usable = max(visible_length, len(markdown.strip())) >= (
                self._policy.http_min_text_length
            )
            success = bool(result.success) and rendered_content_usable and (result.status_code is None or result.status_code < 400)
            failure_kind = None
            if not success:
                failure_kind = ('access_restricted' if result.status_code in (401, 403) else
                    'transient_http' if result.status_code in (408, 425, 429, 500, 502, 503, 504) else
                    'permanent_http' if result.status_code and result.status_code >= 400 else 'render_failed')
            error = result.error_message or None
            if not success and not error:
                error = "Browser rendering did not produce enough visible text"
            return CrawledPage(
                url=url,
                final_url=final_url,
                success=success,
                status_code=result.status_code,
                title=metadata.get("title"),
                html=html or None,
                markdown=markdown or None,
                content_hash=(
                    hashlib.sha256(html.encode("utf-8")).hexdigest() if html else None
                ),
                cache_status=str(result.cache_status) if result.cache_status else None,
                error=error,
                metadata={
                    **metadata,
                    "fetch_strategy": "browser",
                    "fallback_reason": fallback_reason,
                    "browser_success": success,
                    "visible_text_length": visible_length,
                    **({'failure_kind': failure_kind} if failure_kind else {}),
                },
            )
        except Exception as exc:
            return CrawledPage(
                url=url,
                success=False,
                error=str(exc),
                metadata={
                    "fetch_strategy": "browser",
                    "fallback_reason": fallback_reason,
                    "browser_success": False,
                    'failure_kind': 'validation' if isinstance(exc, UnsafeUrlError) else 'robots' if isinstance(exc, PermissionError) else 'render_failed',
                },
            )
