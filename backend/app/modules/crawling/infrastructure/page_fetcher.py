"""HTTP-first page fetching with browser fallback and batch deduplication."""

import asyncio
import time
import math
from contextlib import AsyncExitStack
from collections.abc import Awaitable, Callable

import httpx
from crawl4ai import AsyncWebCrawler

from app.crawler.schemas import CrawlPolicy, CrawledPage, DiscoveredLink
from app.modules.crawling.infrastructure.browser_renderer import BrowserPageRenderer
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy
from app.modules.crawling.infrastructure.fetch_resilience import ResilientHttpFetcher
from app.modules.crawling.infrastructure.http_page_fetcher import HttpFetchOutcome, HttpPageFetcher
from app.modules.crawling.ports.checkpoint import CrawlCheckpointPort


HttpGet = Callable[..., Awaitable[httpx.Response]]
RobotsCheck = Callable[[str], Awaitable[bool]]


async def _bounded_results(awaitables, deadline: float):
    """Retain finished page work and drain cancelled tasks before returning."""
    tasks = [asyncio.create_task(item) for item in awaitables]
    if not tasks:
        return []
    try:
        _, pending = await asyncio.wait(tasks, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        return [None if task.cancelled() else task.result() for task in tasks]
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _budget_page(url: str, strategy: str) -> CrawledPage:
    return CrawledPage(url=url, success=False, error="Page is queued for crawl continuation",
                       metadata={"fetch_strategy": strategy, "failure_kind": "pending", "retryable": True})


class Crawl4AiPageFetcher:
    """Coordinate HTTP-first fetching, optional browser fallback and deduplication."""

    def __init__(
        self,
        policy: CrawlPolicy,
        user_agent: str,
        egress: CrawlEgressPolicy | None = None,
        *, batch_timeout_seconds: float = 120.0,
        checkpoint: CrawlCheckpointPort | None = None,
    ) -> None:
        self._policy = policy
        self._http = ResilientHttpFetcher(policy, HttpPageFetcher(policy, egress))
        self._browser = BrowserPageRenderer(policy, user_agent, egress)
        self._batch_timeout_seconds = batch_timeout_seconds
        self._checkpoint = checkpoint
        self._egress = egress

    def create_crawler(self) -> AsyncWebCrawler:
        return self._browser.create_crawler()

    def _should_render(self, outcome: HttpFetchOutcome) -> tuple[bool, str]:
        failure_kind = str(outcome.page.metadata.get("failure_kind") or "")
        allowed_failure = failure_kind not in {
            "access_restricted",
            "circuit_open",
            "content_type",
            "permanent_http",
            "robots",
            "validation",
        }
        explicitly_requested = self._policy.render_javascript and allowed_failure
        should_render = self._policy.browser_fallback_enabled and (
            outcome.requires_browser or explicitly_requested
        )
        reason = outcome.fallback_reason or (
            "JavaScript rendering explicitly requested"
            if explicitly_requested
            else "HTTP stage requested browser fallback"
        )
        return should_render, reason

    @staticmethod
    def _combine_browser_result(
        http_outcome: HttpFetchOutcome,
        browser_page: CrawledPage,
    ) -> CrawledPage:
        metadata = {
            **http_outcome.page.metadata,
            **browser_page.metadata,
            "http_status_code": http_outcome.page.status_code,
            "http_error": http_outcome.page.error,
        }
        error = browser_page.error
        if not browser_page.success and http_outcome.page.error:
            error = f"HTTP: {http_outcome.page.error}; Browser: {browser_page.error}"
        return browser_page.model_copy(update={"metadata": metadata, "error": error})

    async def fetch_page(
        self,
        crawler: AsyncWebCrawler,
        source_url: str,
        url: str,
        fetch_http: HttpGet,
        robots_check: RobotsCheck,
    ) -> CrawledPage:
        outcome = await self._http.fetch(source_url, url, fetch_http, robots_check)
        should_render, reason = self._should_render(outcome)
        if not should_render:
            return outcome.page
        browser_page = await self._browser.render(
            crawler,
            source_url,
            url,
            robots_check,
            reason,
        )
        return self._combine_browser_result(outcome, browser_page)

    async def fetch_all(
        self, source_url, links, fetch_http, robots_check,
    ) -> list[CrawledPage]:
        async with AsyncExitStack() as stack:
            self._render_stack, self._renderer = stack, None
            return await self._fetch_all(source_url, links, fetch_http, robots_check)

    async def _fetch_all(
        self,
        source_url: str,
        links: list[DiscoveredLink],
        fetch_http: HttpGet,
        robots_check: RobotsCheck,
    ) -> list[CrawledPage]:
        # A slice is a scheduling turn, not a failed fetch of every queued URL.
        deadline = asyncio.get_running_loop().time() + self._batch_timeout_seconds
        records = await self._checkpoint.load() if self._checkpoint else {}
        cooldown = float(records.get('host_cooldown', {}).get('until', 0))
        self._cooldown_until = cooldown
        pages = [_budget_page(str(link.url), 'http') for link in links]
        remaining = []
        for index, link in enumerate(links):
            cached = records.get('page:' + str(link.url))
            if cached:
                page = CrawledPage.model_validate(cached)
                if str(page.url) != str(link.url):
                    raise ValueError('Crawl checkpoint URL mismatch')
                pages[index] = page
                if page.success or not page.metadata.get('retryable', False):
                    pages[index] = page.model_copy(update={'metadata': {**page.metadata, 'checkpoint_hit': True}})
                    continue
            remaining.append(index)
        if cooldown > time.time():
            return pages
        # Give fresh URLs coverage before retrying an already-failed request.
        remaining.sort(key=lambda i: pages[i].metadata.get('failure_kind') != 'pending')
        for start in range(0, len(remaining), self._policy.page_batch_size):
            if asyncio.get_running_loop().time() >= deadline:
                break
            queue = asyncio.Queue()
            for index in remaining[start:start + self._policy.page_batch_size]:
                queue.put_nowait(index)
            render_jobs = []

            async def receive():
                while not queue.empty() and asyncio.get_running_loop().time() < deadline:
                    index = queue.get_nowait()
                    link = links[index]
                    cached_page = pages[index]
                    if self._cooldown_until > time.time():
                        break
                    if cached_page.metadata.get('needs_browser'):
                        render_jobs.append((index, link, HttpFetchOutcome(cached_page, requires_browser=True), 'Resume browser rendering'))
                        continue
                    try:
                        attempt_seconds = self._policy.page_attempt_timeout_seconds
                        if not self._checkpoint:
                            attempt_seconds = min(attempt_seconds, max(.001, deadline - asyncio.get_running_loop().time()))
                        async with asyncio.timeout(attempt_seconds):
                            outcome = await self._http.fetch(source_url, str(link.url), fetch_http, robots_check)
                    except TimeoutError:
                        previous = getattr(self._http, '_last_outcomes', {}).get(str(link.url))
                        active = getattr(self._http, '_active_urls', None)
                        if previous is not None:
                            # Preserve a known 429/503 instead of relabelling its
                            # interrupted backoff as an unexplained network error.
                            outcome = previous
                        elif active is not None and str(link.url) not in active:
                            outcome = HttpFetchOutcome(_budget_page(str(link.url), 'http'), retryable=True)
                        else:
                            outcome = HttpFetchOutcome(CrawledPage(url=link.url, success=False, error='Page request deadline exceeded',
                                metadata={'fetch_strategy': 'http', 'failure_kind': 'transport', 'attempts': 1}), retryable=True)
                    kind = outcome.page.metadata.get('failure_kind')
                    attempts = int(cached_page.metadata.get('http_turns', 0)) + int(kind not in ('pending', 'circuit_open'))
                    retryable = outcome.retryable or outcome.requires_browser or kind == 'circuit_open'
                    metadata = {**outcome.page.metadata, 'http_turns': attempts,
                                'needs_browser': outcome.requires_browser,
                                'retryable': retryable and (outcome.requires_browser or attempts < 3)}
                    if outcome.page.status_code in (429, 503) or kind == 'circuit_open':
                        retry_after = float(outcome.page.metadata.get('retry_after_seconds', 0))
                        retry_after = min(86400, retry_after) if math.isfinite(retry_after) else 60
                        delay = max(10, retry_after,
                                    self._policy.circuit_breaker_cooldown_seconds if kind == 'circuit_open' else 0)
                        self._cooldown_until = max(self._cooldown_until, time.time() + delay)
                        if self._checkpoint:
                            await self._checkpoint.save('host_cooldown', {'until': self._cooldown_until})
                    page = self._with_discovery_metadata(outcome.page.model_copy(update={'metadata': metadata}), link)
                    pages[index] = page
                    if self._checkpoint:
                        await self._checkpoint.save('page:' + str(link.url), page.model_dump(mode='json'))
                    should_render, reason = self._should_render(outcome)
                    if should_render:
                        if self._egress and self._egress.should_use_worker(str(link.url)):
                            # Rendering must never bypass a worker-only egress route.
                            if not page.success:
                                page = page.model_copy(update={'error': 'Rendering unavailable on the permitted source egress route',
                                    'metadata': {**page.metadata, 'failure_kind': 'render_unavailable', 'retryable': False}})
                                pages[index] = page
                                if self._checkpoint:
                                    await self._checkpoint.save('page:' + str(link.url), page.model_dump(mode='json'))
                        else:
                            render_jobs.append((index, link, outcome, reason))

            workers = [asyncio.create_task(receive()) for _ in range(min(self._policy.max_concurrency, queue.qsize()))]
            try:
                await asyncio.gather(*workers)
            finally:
                for worker in workers:
                    if not worker.done():
                        worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
            if render_jobs and asyncio.get_running_loop().time() < deadline:
                await self._render_batch(source_url, render_jobs, pages, robots_check, deadline)
        # Account for every selected URL. Content dedupe belongs to preparation.
        return [self._with_discovery_metadata(page, link) for page, link in zip(pages, links, strict=True)]

    async def _render_batch(self, source_url, jobs, pages, robots_check, deadline):
        # A queued browser page has not used an attempt. Persist this explicitly
        # so the next scheduling slice resumes rendering, not HTTP acquisition.
        for index, link, _, _ in jobs:
            if not pages[index].success:
                pages[index] = pages[index].model_copy(update={'metadata': {
                    **pages[index].metadata, 'fetch_strategy': 'browser', 'needs_browser': True, 'failure_kind': 'pending', 'retryable': True}})
                if self._checkpoint:
                    await self._checkpoint.save('page:' + str(link.url), pages[index].model_dump(mode='json'))
        page_seconds = min(60, self._policy.browser_timeout_ms / 1000)
        render_deadline = deadline + page_seconds if self._checkpoint else deadline
        semaphore = asyncio.Semaphore(min(3, self._policy.max_concurrency))
        if self._renderer is None:
            try:
                async with asyncio.timeout(min(20, max(.001, deadline - asyncio.get_running_loop().time()))):
                    self._renderer = await self._render_stack.enter_async_context(self.create_crawler())
            except Exception:
                # Catch only startup errors. Persistence/fence failures below
                # must propagate, never be mistaken for a browser failure.
                for index, link, _, _ in jobs:
                    if not pages[index].success:
                        attempts = int(pages[index].metadata.get('render_turns', 0)) + 1
                        page = pages[index].model_copy(update={'error': 'Browser startup unavailable', 'metadata': {
                            **pages[index].metadata, 'failure_kind': 'render_startup',
                            'render_turns': attempts, 'retryable': attempts < 3}})
                        pages[index] = page
                        if self._checkpoint:
                            await self._checkpoint.save('page:' + str(link.url), page.model_dump(mode='json'))
                return
        crawler = self._renderer
        async def render_one(index, link, outcome, reason):
            async with semaphore:
                if asyncio.get_running_loop().time() >= deadline:
                    return
                previous = pages[index]
                attempts = int(previous.metadata.get('render_turns', 0)) + 1
                try:
                    async with asyncio.timeout(page_seconds):
                        rendered = await self._browser.render(crawler, source_url, str(link.url), robots_check, reason)
                except TimeoutError:
                    rendered = CrawledPage(url=link.url, success=False, error='Browser page deadline exceeded',
                                           metadata={'failure_kind': 'render_timeout', 'fetch_strategy': 'browser'})
                combined = self._combine_browser_result(outcome, rendered)
                if combined.success or not previous.success:
                    kind = None if combined.success else rendered.metadata.get('failure_kind') or 'render_failed'
                    permanent = kind in ('robots', 'validation', 'access_restricted', 'permanent_http')
                    metadata = {**combined.metadata, 'render_turns': attempts, 'needs_browser': not combined.success,
                                'retryable': not combined.success and not permanent and attempts < 3}
                    if kind:
                        metadata['failure_kind'] = kind
                    else:
                        metadata.pop('failure_kind', None)
                    combined = self._with_discovery_metadata(combined.model_copy(update={'metadata': metadata}), link)
                    pages[index] = combined
                    if self._checkpoint:
                        await self._checkpoint.save('page:' + str(link.url), combined.model_dump(mode='json'))
        await _bounded_results((render_one(*job) for job in jobs), render_deadline)

    @staticmethod
    def _with_discovery_metadata(page: CrawledPage, link: DiscoveredLink) -> CrawledPage:
        metadata = {
            **page.metadata,
            "discovered_via": link.discovered_via,
            "discovery_methods": list(link.discovery_methods or [link.discovered_via]),
        }
        if link.published_at is not None:
            metadata["discovered_published_at"] = link.published_at.isoformat()
        return page.model_copy(update={"metadata": metadata})

    @staticmethod
    def _deduplicate(pages: list[CrawledPage]) -> list[CrawledPage]:
        unique_pages: list[CrawledPage] = []
        seen_hashes: set[str] = set()
        for page in pages:
            if page.content_hash and page.content_hash in seen_hashes:
                continue
            if page.content_hash:
                seen_hashes.add(page.content_hash)
            unique_pages.append(page)
        return unique_pages
