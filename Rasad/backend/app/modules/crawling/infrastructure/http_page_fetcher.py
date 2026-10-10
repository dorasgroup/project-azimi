"""Convert one validated HTTP response into a typed crawled-page result."""

import hashlib
import html as html_module
import re
from email.utils import parsedate_to_datetime
from datetime import UTC, datetime
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from app.crawler.schemas import CrawlPolicy, CrawledPage
from app.crawler.security import UnsafeUrlError, host_is_allowed
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy


HttpGet = Callable[..., Awaitable[httpx.Response]]
RobotsCheck = Callable[[str], Awaitable[bool]]
RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
RESTRICTED_STATUSES = {401, 403}
JAVASCRIPT_MARKERS = (
    "__next_data__",
    "__nuxt__",
    "data-reactroot",
    "id=\"root\"",
    "id='root'",
    "enable javascript",
    "javascript is required",
)


@dataclass(frozen=True)
class HttpFetchOutcome:
    page: CrawledPage
    retryable: bool = False
    requires_browser: bool = False
    fallback_reason: str | None = None


def _visible_text_length(html: str) -> int:
    without_scripts = re.sub(
        r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)>",
        " ",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<[^>]+>", " ", without_scripts)
    return len(" ".join(html_module.unescape(text).split()))


def _title(html: str) -> str | None:
    match = re.search(r"<title\b[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return " ".join(html_module.unescape(match.group(1)).split()) or None


class HttpPageFetcher:
    """Fetch one page over HTTP and classify whether browser rendering is needed."""

    def __init__(
        self,
        policy: CrawlPolicy,
        egress: CrawlEgressPolicy | None = None,
    ) -> None:
        self._policy = policy
        self._egress = egress or CrawlEgressPolicy()

    def requires_browser(self, html: str) -> tuple[bool, str | None]:
        lowered = html.casefold()
        marker_found = any(marker in lowered for marker in JAVASCRIPT_MARKERS)
        visible_length = _visible_text_length(html)
        if marker_found and visible_length < self._policy.http_min_text_length:
            return True, "JavaScript application shell detected"
        if (
            visible_length < self._policy.http_min_text_length
            and "<script" in lowered
        ):
            return True, "HTTP response does not contain enough rendered text"
        return False, None

    async def fetch(
        self,
        source_url: str,
        url: str,
        fetch_http: HttpGet,
        robots_check: RobotsCheck,
    ) -> HttpFetchOutcome:
        try:
            await self._egress.validate(url)
            if not host_is_allowed(url, source_url, self._policy.allow_subdomains):
                raise UnsafeUrlError("URL is outside the allowed source host")
            if not await robots_check(url):
                return HttpFetchOutcome(
                    CrawledPage(
                        url=url,
                        success=False,
                        error="Blocked by robots.txt",
                        metadata={"fetch_strategy": "http", "failure_kind": "robots"},
                    )
                )

            response = await fetch_http(url)
            final_url = str(response.url)
            await self._egress.validate(final_url)
            if not host_is_allowed(final_url, source_url, self._policy.allow_subdomains):
                raise UnsafeUrlError("Redirected outside the allowed source host")
            content_type = response.headers.get("content-type", "").casefold()
            body = response.text or ""
            looks_like_html = "<html" in body[:1000].casefold() or "text/html" in content_type
            metadata = {
                "fetch_strategy": "http",
                "content-type": content_type,
                "response_bytes": len(response.content),
                "error_origin": 'worker' if response.headers.get('X-Rasad-Worker-Error') else 'upstream',
            }

            if response.status_code in RETRYABLE_STATUSES:
                retry_after = response.headers.get('Retry-After', '')
                try:
                    delay = float(retry_after)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        delay = 0
                return HttpFetchOutcome(
                    CrawledPage(
                        url=url,
                        final_url=final_url,
                        success=False,
                        status_code=response.status_code,
                        error=f"HTTP {response.status_code}",
                        metadata={**metadata, "failure_kind": "transient_http", 'retry_after_seconds': max(0, delay)},
                    ),
                    retryable=True,
                )
            if response.status_code in RESTRICTED_STATUSES:
                return HttpFetchOutcome(
                    CrawledPage(
                        url=url,
                        final_url=final_url,
                        success=False,
                        status_code=response.status_code,
                        error=f"HTTP {response.status_code}: access restricted",
                        metadata={**metadata, "failure_kind": "access_restricted"},
                    )
                )
            if response.status_code >= 400:
                return HttpFetchOutcome(
                    CrawledPage(
                        url=url,
                        final_url=final_url,
                        success=False,
                        status_code=response.status_code,
                        error=f"HTTP {response.status_code}",
                        metadata={**metadata, "failure_kind": "permanent_http"},
                    )
                )
            if not looks_like_html:
                return HttpFetchOutcome(
                    CrawledPage(
                        url=url,
                        final_url=final_url,
                        success=False,
                        status_code=response.status_code,
                        error=f"Unsupported content type: {content_type or 'unknown'}",
                        metadata={**metadata, "failure_kind": "content_type"},
                    )
                )

            requires_browser, reason = self.requires_browser(body)
            page = CrawledPage(
                url=url,
                final_url=final_url,
                success=not requires_browser,
                status_code=response.status_code,
                title=_title(body),
                html=body or None,
                content_hash=(
                    hashlib.sha256(body.encode("utf-8")).hexdigest() if body else None
                ),
                error=reason if requires_browser else None,
                metadata={
                    **metadata,
                    "visible_text_length": _visible_text_length(body),
                },
            )
            return HttpFetchOutcome(
                page,
                requires_browser=requires_browser,
                fallback_reason=reason,
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            return HttpFetchOutcome(
                CrawledPage(
                    url=url,
                    success=False,
                    error=str(exc),
                    metadata={"fetch_strategy": "http", "failure_kind": "transport"},
                ),
                retryable=True,
            )
        except Exception as exc:
            return HttpFetchOutcome(
                CrawledPage(
                    url=url,
                    success=False,
                    error=str(exc),
                    metadata={"fetch_strategy": "http", "failure_kind": "validation"},
                )
            )
