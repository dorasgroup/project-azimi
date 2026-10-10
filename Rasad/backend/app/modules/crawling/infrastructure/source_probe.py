"""Source reachability probe."""

import time
from collections.abc import Awaitable, Callable

import httpx

from app.crawler.schemas import SourceProbe
from app.modules.crawling.infrastructure.http_transport import (
    ACCESS_RESTRICTED_STATUSES,
    access_restricted_reason,
    has_usable_html,
)


HttpGet = Callable[..., Awaitable[httpx.Response]]
RobotsCheck = Callable[[str], Awaitable[bool]]


class SourceProbeService:
    """Measure source reachability and report its robots.txt decision."""

    async def probe(self, url: str, fetch: HttpGet, robots_check: RobotsCheck) -> SourceProbe:
        started = time.perf_counter()
        try:
            response = await fetch(url)
            final_url = str(response.url)
            fallback_html = has_usable_html(
                response.status_code,
                response.headers.get("content-type"),
                response.text,
            )
            access_restricted = response.status_code in ACCESS_RESTRICTED_STATUSES
            return SourceProbe(
                requested_url=url,
                final_url=final_url,
                success=response.status_code < 400 or fallback_html or access_restricted,
                retryable=response.status_code in {408, 425, 429, 500, 502, 503, 504},
                http_status=response.status_code,
                response_time_ms=round((time.perf_counter() - started) * 1000),
                content_type=response.headers.get("content-type"),
                redirected=final_url.rstrip("/") != url.rstrip("/"),
                robots_allowed=await robots_check(final_url),
                reason=(
                    f"HTTP {response.status_code} with usable HTML"
                    if fallback_html
                    else access_restricted_reason(response.status_code)
                    if access_restricted
                    else None
                    if response.status_code < 400
                    else f"HTTP {response.status_code}"
                ),
            )
        except Exception as exc:
            return SourceProbe(
                requested_url=url,
                success=False,
                retryable=isinstance(exc, (httpx.TimeoutException, httpx.TransportError)),
                response_time_ms=round((time.perf_counter() - started) * 1000),
                reason=str(exc),
            )
