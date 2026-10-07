from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import httpx
import pytest

from app.crawler.schemas import CrawlPolicy
from app.modules.crawling.infrastructure.fetch_resilience import ResilientHttpFetcher
from app.modules.crawling.infrastructure.http_page_fetcher import HttpPageFetcher


SOURCE_URL = "https://news.example.com"
PAGE_URL = f"{SOURCE_URL}/story"


async def _valid_url(url: str) -> str:
    return url


class _AllowAllEgress:
    proxy_url = None

    async def validate(self, url: str) -> str:
        return url


async def _robots_allowed(_url: str) -> bool:
    return True


def _response(status: int, body: str, content_type: str = "text/html") -> httpx.Response:
    request = httpx.Request("GET", PAGE_URL)
    return httpx.Response(
        status,
        text=body,
        headers={"content-type": content_type},
        request=request,
    )


@asynccontextmanager
async def _validated_urls() -> AsyncIterator[None]:
    with (
        patch(
            "app.modules.crawling.infrastructure.http_page_fetcher.host_is_allowed",
            return_value=True,
        ),
    ):
        yield


@pytest.mark.asyncio
async def test_http_fetcher_keeps_static_html_out_of_browser() -> None:
    policy = CrawlPolicy(http_min_text_length=40)
    fetcher = HttpPageFetcher(policy, _AllowAllEgress())

    async def fetch_http(_url: str) -> httpx.Response:
        return _response(200, f"<html><title>خبر</title><body>{'متن خبر ' * 30}</body></html>")

    async with _validated_urls():
        outcome = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert outcome.page.success is True
    assert outcome.requires_browser is False
    assert outcome.page.title == "خبر"
    assert outcome.page.metadata["fetch_strategy"] == "http"


@pytest.mark.asyncio
async def test_http_fetcher_detects_javascript_shell() -> None:
    fetcher = HttpPageFetcher(CrawlPolicy(), _AllowAllEgress())
    shell = '<html><body><div id="root"></div><script src="app.js"></script></body></html>'

    async def fetch_http(_url: str) -> httpx.Response:
        return _response(200, shell)

    async with _validated_urls():
        outcome = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert outcome.page.success is False
    assert outcome.requires_browser is True
    assert outcome.fallback_reason == "JavaScript application shell detected"


@pytest.mark.asyncio
async def test_http_fetcher_detects_large_script_shell_with_little_visible_text() -> None:
    fetcher = HttpPageFetcher(CrawlPolicy(), _AllowAllEgress())
    shell = (
        "<html><body><main>در حال بارگذاری</main>"
        f"<script>{'const payload = 1;' * 400}</script></body></html>"
    )

    async def fetch_http(_url: str) -> httpx.Response:
        return _response(200, shell)

    async with _validated_urls():
        outcome = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert len(shell) > fetcher._policy.http_min_content_length
    assert outcome.requires_browser is True
    assert outcome.fallback_reason == "HTTP response does not contain enough rendered text"


@pytest.mark.asyncio
async def test_restricted_response_never_requests_browser_bypass() -> None:
    fetcher = HttpPageFetcher(CrawlPolicy(), _AllowAllEgress())

    async def fetch_http(_url: str) -> httpx.Response:
        return _response(403, "Forbidden", "text/plain")

    async with _validated_urls():
        outcome = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert outcome.page.success is False
    assert outcome.requires_browser is False
    assert outcome.page.metadata["failure_kind"] == "access_restricted"


@pytest.mark.asyncio
async def test_transient_response_retries_then_caches_success() -> None:
    policy = CrawlPolicy(
        retry_count=2,
        request_delay_ms=0,
        retry_backoff_base_ms=1,
        http_min_text_length=40,
    )
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    fetcher = ResilientHttpFetcher(
        policy,
        HttpPageFetcher(policy, _AllowAllEgress()),
        sleep=sleep,
    )
    responses = iter(
        [
            _response(503, "Unavailable", "text/plain"),
            _response(200, f"<html><body>{'خبر معتبر ' * 30}</body></html>"),
        ]
    )
    calls = 0

    async def fetch_http(_url: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        return next(responses)

    async with _validated_urls():
        first = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)
        second = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert first.page.success is True
    assert first.page.metadata["attempts"] == 2
    assert second.page.metadata["cache_hit"] is True
    assert calls == 2
    assert sleeps == [0.001]


@pytest.mark.asyncio
async def test_circuit_breaker_stops_repeated_transient_failures() -> None:
    policy = CrawlPolicy(
        retry_count=0,
        request_delay_ms=0,
        circuit_breaker_threshold=2,
    )
    fetcher = ResilientHttpFetcher(
        policy,
        HttpPageFetcher(policy, _AllowAllEgress()),
    )
    calls = 0

    async def fetch_http(_url: str) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _response(503, "Unavailable", "text/plain")

    async with _validated_urls():
        await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)
        await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)
        blocked = await fetcher.fetch(SOURCE_URL, PAGE_URL, fetch_http, _robots_allowed)

    assert calls == 2
    assert blocked.page.metadata["failure_kind"] == "circuit_open"
    assert blocked.page.metadata["attempts"] == 0
