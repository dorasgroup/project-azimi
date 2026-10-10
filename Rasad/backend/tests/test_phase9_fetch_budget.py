"""Slow page tails retain prior evidence without hiding incomplete coverage."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
import pytest
from app.crawler.schemas import CrawlPolicy, CrawledPage, DiscoveredLink
from app.modules.crawling.infrastructure.http_page_fetcher import HttpFetchOutcome
from app.modules.crawling.infrastructure.page_fetcher import Crawl4AiPageFetcher, _bounded_results

URL = 'https://example.invalid/news/'


@pytest.mark.asyncio
async def test_http_tail_timeout_preserves_completed_pages_and_drains_tasks():
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', batch_timeout_seconds=.03)
    cancelled = []
    async def fetch(source, url, *args):
        if url.endswith('2'):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(url)
        return HttpFetchOutcome(CrawledPage(url=url, success=True, html='valid'))
    fetcher._http = SimpleNamespace(fetch=fetch)
    pages = await fetcher.fetch_all(URL, [DiscoveredLink(url=URL+str(i), discovered_via='html') for i in (1,2)], None, None)
    assert pages[0].success and not pages[1].success
    assert pages[1].metadata['failure_kind'] == 'transport'
    assert pages[1].metadata['retryable'] is True
    assert pages[1].metadata['discovered_via'] == 'html' and cancelled == [URL+'2']


@pytest.mark.asyncio
async def test_browser_tail_preserves_http_and_finished_browser_pages():
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', batch_timeout_seconds=.04)
    cancelled = []
    async def fetch(source, url, *args):
        return HttpFetchOutcome(CrawledPage(url=url, success=url.endswith('1'), html='valid'),
                                requires_browser=not url.endswith('1'))
    async def render(crawler, source, url, *args):
        if url.endswith('3'):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(url)
        return CrawledPage(url=url, success=True, html='rendered')
    @asynccontextmanager
    async def crawler():
        yield object()
    fetcher._http = SimpleNamespace(fetch=fetch)
    fetcher._browser = SimpleNamespace(render=render)
    fetcher.create_crawler = crawler
    pages = await fetcher.fetch_all(URL, [DiscoveredLink(url=URL+str(i), discovered_via='html') for i in (1,2,3)], None, None)
    assert [p.success for p in pages] == [True,True,False]
    assert pages[2].metadata['fetch_strategy'] == 'browser' and cancelled == [URL+'3']


@pytest.mark.asyncio
async def test_external_cancel_is_propagated_and_children_are_drained():
    started, drained = asyncio.Event(), asyncio.Event()
    async def child():
        started.set()
        try:
            await asyncio.sleep(10)
        finally:
            drained.set()
    task = asyncio.create_task(_bounded_results([child()], asyncio.get_running_loop().time()+10))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert drained.is_set()


@pytest.mark.asyncio
async def test_completed_failure_is_not_replaced_with_success():
    async def failed():
        raise RuntimeError('failure')
    with pytest.raises(RuntimeError):
        await _bounded_results([failed()], asyncio.get_running_loop().time()+1)


@pytest.mark.asyncio
async def test_empty_batch_is_valid_and_has_no_tasks():
    assert await _bounded_results([], asyncio.get_running_loop().time()) == []
