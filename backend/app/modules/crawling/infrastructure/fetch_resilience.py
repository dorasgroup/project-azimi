"""Per-domain retry, throttling, cache and circuit-breaker policies."""

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from urllib.parse import urlparse

from app.crawler.schemas import CrawlPolicy, CrawledPage
from app.modules.crawling.infrastructure.http_page_fetcher import (
    HttpFetchOutcome,
    HttpGet,
    HttpPageFetcher,
    RobotsCheck,
)


Sleep = Callable[[float], Awaitable[None]]


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


class DomainCircuitBreaker:
    """Stop transient requests to one failing host for a bounded cooldown."""

    def __init__(self, threshold: int, cooldown_seconds: int) -> None:
        self._threshold = threshold
        self._cooldown_seconds = cooldown_seconds
        self._states: dict[str, _CircuitState] = {}

    def allows(self, host: str) -> bool:
        state = self._states.setdefault(host, _CircuitState())
        if state.opened_at is None:
            return True
        if time.monotonic() - state.opened_at >= self._cooldown_seconds:
            state.failures = 0
            state.opened_at = None
            return True
        return False

    def record_success(self, host: str) -> None:
        self._states[host] = _CircuitState()

    def record_failure(self, host: str) -> None:
        state = self._states.setdefault(host, _CircuitState())
        state.failures += 1
        if state.failures >= self._threshold and state.opened_at is None:
            state.opened_at = time.monotonic()

    def state(self, host: str) -> str:
        return "closed" if self.allows(host) else "open"


class DomainRequestCoordinator:
    """Enforce per-host concurrency and a minimum interval between request starts."""

    def __init__(self, max_concurrency: int, delay_ms: int, sleep: Sleep = asyncio.sleep) -> None:
        self._max_concurrency = max_concurrency
        self._delay_seconds = delay_ms / 1000
        self._sleep = sleep
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._next_request_at: dict[str, float] = {}

    def _cooldown(self, url: str, seconds: float) -> None:
        host = urlparse(url).netloc.casefold()
        self._next_request_at[host] = max(self._next_request_at.get(host, 0), time.monotonic() + min(60, seconds))

    @asynccontextmanager
    async def slot(self, url: str) -> AsyncIterator[None]:
        host = urlparse(url).netloc.casefold()
        semaphore = self._semaphores.setdefault(host, asyncio.Semaphore(self._max_concurrency))
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with semaphore:
            async with lock:
                wait_seconds = max(0.0, self._next_request_at.get(host, 0.0) - time.monotonic())
                if wait_seconds:
                    await self._sleep(wait_seconds)
                self._next_request_at[host] = time.monotonic() + self._delay_seconds
            yield


class ResilientHttpFetcher:
    """Apply resilience policies around the focused single-attempt HTTP fetcher."""

    def __init__(
        self,
        policy: CrawlPolicy,
        page_fetcher: HttpPageFetcher,
        *,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._policy = policy
        self._page_fetcher = page_fetcher
        self._sleep = sleep
        self._coordinator = DomainRequestCoordinator(
            policy.max_concurrency,
            policy.request_delay_ms,
            sleep,
        )
        self._circuit = DomainCircuitBreaker(
            policy.circuit_breaker_threshold,
            policy.circuit_breaker_cooldown_seconds,
        )
        self._cache: dict[str, CrawledPage] = {}
        self._last_outcomes: dict[str, HttpFetchOutcome] = {}
        self._active_urls: set[str] = set()

    async def fetch(
        self,
        source_url: str,
        url: str,
        fetch_http: HttpGet,
        robots_check: RobotsCheck,
    ) -> HttpFetchOutcome:
        cached = self._cache.get(url) if self._policy.http_cache_enabled else None
        if cached is not None:
            metadata = {**cached.metadata, "cache_hit": True}
            return HttpFetchOutcome(cached.model_copy(update={"metadata": metadata}))

        host = urlparse(url).netloc.casefold()
        if not self._circuit.allows(host):
            return HttpFetchOutcome(
                CrawledPage(
                    url=url,
                    success=False,
                    error="Circuit breaker is open for source host",
                    metadata={
                        "fetch_strategy": "http",
                        "failure_kind": "circuit_open",
                        "circuit_state": "open",
                        "attempts": 0,
                    },
                )
            )

        final_outcome: HttpFetchOutcome | None = None
        for attempt in range(1, self._policy.retry_count + 2):
            started = time.perf_counter()
            async with self._coordinator.slot(url):
                if not self._circuit.allows(host):
                    return HttpFetchOutcome(CrawledPage(url=url, success=False, error='Circuit breaker is open for source host',
                        metadata={'fetch_strategy': 'http', 'failure_kind': 'circuit_open', 'attempts': 0}), retryable=True)
                self._active_urls.add(url)
                outcome = await self._page_fetcher.fetch(
                    source_url,
                    url,
                    fetch_http,
                    robots_check,
                )
                self._active_urls.discard(url)
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            metadata = {
                **outcome.page.metadata,
                "attempts": attempt,
                "response_time_ms": elapsed_ms,
                "circuit_state": self._circuit.state(host),
                "cache_hit": False,
            }
            outcome = HttpFetchOutcome(
                outcome.page.model_copy(update={"metadata": metadata}),
                retryable=outcome.retryable,
                requires_browser=outcome.requires_browser,
                fallback_reason=outcome.fallback_reason,
            )
            final_outcome = outcome
            self._last_outcomes[url] = outcome
            if outcome.page.status_code == 429:
                delay = max(1, float(outcome.page.metadata.get('retry_after_seconds', 0)))
                self._coordinator._cooldown(url, delay)

            if outcome.page.success or outcome.requires_browser:
                self._circuit.record_success(host)
                if outcome.page.success and self._policy.http_cache_enabled:
                    self._cache[url] = outcome.page
                return outcome
            if not outcome.retryable:
                return outcome

            self._circuit.record_failure(host)
            if attempt <= self._policy.retry_count and self._circuit.allows(host):
                backoff_ms = min(
                    self._policy.retry_backoff_base_ms * (2 ** (attempt - 1)),
                    self._policy.retry_backoff_max_ms,
                )
                if backoff_ms:
                    await self._sleep(backoff_ms / 1000)
            elif not self._circuit.allows(host):
                break

        assert final_outcome is not None
        metadata = {
            **final_outcome.page.metadata,
            "circuit_state": self._circuit.state(host),
        }
        return HttpFetchOutcome(
            final_outcome.page.model_copy(update={"metadata": metadata}),
            retryable=final_outcome.retryable,
        )
