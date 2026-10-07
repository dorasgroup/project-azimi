"""Regression gates for bounded, truthful and restart-safe crawl slices."""
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest

from app.crawler.schemas import CrawlPolicy, CrawledPage, DiscoveredLink, SourceProbe
from app.modules.crawling.infrastructure.page_fetcher import Crawl4AiPageFetcher
from app.modules.crawling.infrastructure.http_page_fetcher import HttpFetchOutcome, HttpPageFetcher
from app.modules.crawling.infrastructure.crawl_diagnostics import CrawlDiagnosticsBuilder
from app.modules.crawling.infrastructure.crawl_orchestrator import CrawlOrchestrator
from app.modules.crawling.infrastructure.candidate_ranker import CandidateRanker
from app.modules.monitoring.infrastructure.crawl_checkpoint import FencedCrawlCheckpoint, CrawlCheckpointFenceLost
from app.modules.monitoring.domain.execution import SourceExecutionSpec, ClaimOutcome, MonitoringRunStatus
from app.modules.monitoring.application.execute_source_task import ExecuteSourceTaskUseCase
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy


class MemoryCheckpoint:
    def __init__(self): self.records = {}
    async def load(self): return dict(self.records)
    async def save(self, key, value): self.records[key] = value
    async def discard(self): self.records = {}


def links(count):
    return [DiscoveredLink(url=f'https://example.com/news/{i}', discovered_via='rss') for i in range(count)]


@pytest.mark.asyncio
async def test_small_slices_resume_without_repeating_completed_pages():
    checkpoint, completed = MemoryCheckpoint(), Counter()
    selected = links(24)
    async def receive(source, url, *args):
        await asyncio.sleep(.006)
        completed[url] += 1
        return HttpFetchOutcome(CrawledPage(url=url, success=True, html='<html>valid</html>', content_hash='same-content'))
    for _ in range(5):
        fetcher = Crawl4AiPageFetcher(CrawlPolicy(max_concurrency=3), 'test', batch_timeout_seconds=.035, checkpoint=checkpoint)
        fetcher._http = SimpleNamespace(fetch=receive)
        pages = await fetcher.fetch_all('https://example.com', selected, AsyncMock(), AsyncMock())
        assert len(pages) == 24  # duplicates must not hide failed or selected URLs
        if all(page.success for page in pages): break
        assert any(page.metadata.get('failure_kind') == 'pending' for page in pages)
        assert not any(page.metadata.get('failure_kind') == 'batch_time_budget' for page in pages)
    assert all(page.success for page in pages)
    assert set(completed.values()) == {1}
    assert any(page.metadata.get('checkpoint_hit') for page in pages)


@pytest.mark.asyncio
async def test_plan_resume_skips_discovery_but_keeps_probe_gate():
    checkpoint = MemoryCheckpoint()
    policy = CrawlPolicy()
    probe = AsyncMock(return_value=SourceProbe(requested_url='https://example.com', success=True, robots_allowed=True))
    discover = AsyncMock(return_value=links(2))
    fetch = AsyncMock(return_value=[CrawledPage(url=x.url, success=True) for x in links(2)])
    for _ in range(2):
        await CrawlOrchestrator(policy, checkpoint).crawl('https://example.com', 'topic', None, None, probe=probe, discover=discover, fetch_pages=fetch)
    assert discover.await_count == 1 and probe.await_count == 2
    probe.return_value = SourceProbe(requested_url='https://example.com', success=True, robots_allowed=False)
    result = await CrawlOrchestrator(policy, checkpoint).crawl('https://example.com', 'topic', None, None, probe=probe, discover=discover, fetch_pages=fetch)
    assert result.diagnostics.status == 'blocked'
    assert fetch.await_count == 2


def test_pending_urls_are_neither_failed_nor_successfully_fetched():
    selected = links(3)
    pages = [CrawledPage(url=selected[0].url, success=True),
        CrawledPage(url=selected[1].url, success=False, metadata={'failure_kind': 'pending', 'retryable': True}),
        CrawledPage(url=selected[2].url, success=False, metadata={'failure_kind': 'transport', 'retryable': True})]
    now = datetime.now(UTC)
    d = CrawlDiagnosticsBuilder.build(probe=SourceProbe(requested_url='https://example.com', success=True),
        discovered=selected, selected=selected, pages=pages, date_from=None, date_to=None, max_pages=3, started_at=now, finished_at=now)
    assert d.status == 'partial' and d.reason_code == 'crawl_continuation_required'
    assert (d.pending_pages, d.failed_pages, d.retryable_pages, d.fetched_total) == (1, 1, 1, 2)
    assert d.successful_pages == 1 and d.selected_completion_percent == 33.3


@pytest.mark.asyncio
async def test_continuation_does_not_handoff_or_terminalize_source():
    task_id, token, run_id = uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(claim_task_explicit=AsyncMock(return_value=ClaimOutcome.claimed(claim_token=token, lease_expires_at=datetime.now(UTC), attempt_count=1)),
        get_by_id=AsyncMock(return_value=SimpleNamespace(run_id=run_id, source_id=uuid4(), execution_spec=object(), attempt_count=1)),
        defer_task=AsyncMock(return_value=True))
    run_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=run_id, monitoring_job_id=uuid4(), status=MonitoringRunStatus.RUNNING)))
    engine = SimpleNamespace(execute_crawl=AsyncMock(return_value=(SimpleNamespace(diagnostics=SimpleNamespace(pending_pages=10, retryable_pages=0)), object(), object())))
    persister = SimpleNamespace(persist_success=AsyncMock(), persist_failure=AsyncMock())
    handoff = SimpleNamespace(ensure_and_dispatch=AsyncMock())
    use_case = ExecuteSourceTaskUseCase(repository, run_repo, engine, persister, analysis_handoff=handoff, checkpoint_factory=lambda *args: MemoryCheckpoint())
    result = await use_case.execute(task_id)
    assert result['status'] == 'continuation_queued'
    repository.defer_task.assert_awaited_once_with(task_id, claim_token=token)
    persister.persist_success.assert_not_called()
    persister.persist_failure.assert_not_called()
    handoff.ensure_and_dispatch.assert_not_called()


@pytest.mark.asyncio
async def test_checkpoint_survives_new_instance_and_rejects_lost_claim(tmp_path):
    task_id, token = uuid4(), uuid4()
    spec = SourceExecutionSpec(uuid4(), 'topic', date.today(), date.today(), {}, 'https://example.com', 'test')
    session = SimpleNamespace(scalar=AsyncMock(return_value=task_id), commit=AsyncMock())
    @asynccontextmanager
    async def sessions(): yield session
    with patch('app.modules.monitoring.infrastructure.crawl_checkpoint.lock_parent_for_child', new=AsyncMock(return_value='running')):
        first = FencedCrawlCheckpoint(sessions, task_id, token, spec, root=tmp_path)
        await first.save('plan', {'selected': []})
        await first.save('page:https://example.com/news/1', {'url': 'https://example.com/news/1', 'success': True})
        recovered = FencedCrawlCheckpoint(sessions, task_id, uuid4(), spec, root=tmp_path)
        assert len(await recovered.load()) == 2
        session.scalar.return_value = None
        with pytest.raises(CrawlCheckpointFenceLost):
            await first.save('plan', {'selected': ['stale']})
        session.scalar.return_value = task_id
        assert (await FencedCrawlCheckpoint(sessions, task_id, token, spec, root=tmp_path).load())['plan'] == {'selected': []}
        other = SourceExecutionSpec(spec.source_id, 'changed topic', spec.date_from, spec.date_to, {}, spec.base_url, spec.title)
        assert await FencedCrawlCheckpoint(sessions, task_id, token, other, root=tmp_path).load() == {}


def test_broad_date_window_reserves_topical_candidates_from_each_week():
    candidates = [DiscoveredLink(url=f'https://example.com/news/{day}', title='women rights', discovered_via='rss',
        topic_matches=['women'], published_at=datetime(2026, 9, day, tzinfo=UTC)) for day in (1, 8, 15, 22, 29)]
    selected = CandidateRanker(CrawlPolicy(max_pages=5)).rank(candidates, date_from=date(2026, 9, 1), date_to=date(2026, 10, 13), topic='women')
    assert {item.published_at.day for item in selected} == {1, 8, 15, 22, 29}


@pytest.mark.asyncio
async def test_large_403_error_html_is_not_valid_news():
    egress = SimpleNamespace(validate=AsyncMock(side_effect=lambda url: url))
    response = httpx.Response(403, text='<html>' + 'Access denied ' * 300 + '</html>', headers={'content-type': 'text/html'}, request=httpx.Request('GET', 'https://example.com/news/1'))
    result = await HttpPageFetcher(CrawlPolicy(), egress).fetch('https://example.com', 'https://example.com/news/1', AsyncMock(return_value=response), AsyncMock(return_value=True))
    assert not result.page.success and not result.requires_browser
    assert result.page.metadata['failure_kind'] == 'access_restricted'


@pytest.mark.asyncio
async def test_worker_admission_rate_limit_is_retryable_and_distinct_from_origin():
    client = SimpleNamespace(post=AsyncMock(return_value=httpx.Response(429, headers={'X-Rasad-Worker-Error': 'rate_limited', 'Retry-After': '10'})))
    policy = CrawlEgressPolicy(mode='smart', worker_url='https://worker.example', worker_secret='synthetic')
    response = await policy.fetch_through_worker('https://www.bbc.com/news', headers={}, timeout_seconds=10, client=client)
    egress = SimpleNamespace(validate=AsyncMock(side_effect=lambda url: url))
    outcome = await HttpPageFetcher(CrawlPolicy(), egress).fetch('https://www.bbc.com', 'https://www.bbc.com/news', AsyncMock(return_value=response), AsyncMock(return_value=True))
    assert outcome.retryable and outcome.page.metadata['error_origin'] == 'worker'
    assert outcome.page.metadata['retry_after_seconds'] == 10


@pytest.mark.asyncio
async def test_interrupted_rate_limit_backoff_preserves_actual_429_cause():
    egress = SimpleNamespace(validate=AsyncMock(side_effect=lambda url: url))
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(request_delay_ms=0), 'test', batch_timeout_seconds=.025)
    fetcher._http._page_fetcher = HttpPageFetcher(CrawlPolicy(), egress)
    response = httpx.Response(429, headers={'Retry-After': '5'}, request=httpx.Request('GET', 'https://example.com/news/0'))
    pages = await fetcher.fetch_all('https://example.com', links(1), AsyncMock(return_value=response), AsyncMock(return_value=True))
    assert pages[0].status_code == 429
    assert pages[0].metadata['failure_kind'] == 'transient_http'
    assert pages[0].metadata['retryable'] is True


@pytest.mark.asyncio
async def test_pending_work_continues_after_third_task_claim():
    task_id, token, run_id = uuid4(), uuid4(), uuid4()
    repository = SimpleNamespace(claim_task_explicit=AsyncMock(return_value=ClaimOutcome.claimed(token, datetime.now(UTC), 8)),
        get_by_id=AsyncMock(return_value=SimpleNamespace(run_id=run_id, source_id=uuid4(), execution_spec=object(), attempt_count=8)),
        defer_task=AsyncMock(return_value=True))
    run_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=run_id, monitoring_job_id=uuid4(), status=MonitoringRunStatus.RUNNING)))
    engine = SimpleNamespace(execute_crawl=AsyncMock(return_value=(SimpleNamespace(diagnostics=SimpleNamespace(pending_pages=31, retryable_pages=0)), object(), object())))
    persister = SimpleNamespace(persist_success=AsyncMock(), persist_failure=AsyncMock())
    result = await ExecuteSourceTaskUseCase(repository, run_repo, engine, persister, checkpoint_factory=lambda *args: MemoryCheckpoint()).execute(task_id)
    assert result['status'] == 'continuation_queued'
    persister.persist_success.assert_not_called()
    persister.persist_failure.assert_not_called()


@pytest.mark.asyncio
async def test_http_attempt_limits_are_per_page_not_per_scheduling_slice():
    checkpoint = MemoryCheckpoint()
    calls = Counter()
    async def fetch(source, url, *args):
        calls[url] += 1
        return HttpFetchOutcome(CrawledPage(url=url, success=False, status_code=502,
            metadata={'failure_kind': 'transient_http'}), retryable=True)
    for _ in range(5):
        fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', checkpoint=checkpoint)
        fetcher._http = SimpleNamespace(fetch=fetch)
        pages = await fetcher.fetch_all('https://example.com', links(2), AsyncMock(), AsyncMock())
    assert set(calls.values()) == {3}
    assert all(not p.success and not p.metadata['retryable'] for p in pages)


@pytest.mark.asyncio
async def test_browser_waiters_resume_without_refetch_or_consuming_attempts():
    checkpoint, received, rendered, startups = MemoryCheckpoint(), Counter(), Counter(), []
    async def fetch(source, url, *args):
        received[url] += 1
        return HttpFetchOutcome(CrawledPage(url=url, success=False, html='shell'), requires_browser=True)
    async def render(crawler, source, url, *args):
        await asyncio.sleep(.025)
        rendered[url] += 1
        return CrawledPage(url=url, success=True, html='article')
    @asynccontextmanager
    async def crawler():
        startups.append(True)
        yield object()
    for _ in range(8):
        fetcher = Crawl4AiPageFetcher(CrawlPolicy(max_concurrency=1, page_batch_size=2), 'test', batch_timeout_seconds=.01, checkpoint=checkpoint)
        fetcher._http = SimpleNamespace(fetch=fetch)
        fetcher._browser = SimpleNamespace(render=render)
        fetcher.create_crawler = crawler
        pages = await fetcher.fetch_all('https://example.com', links(4), AsyncMock(), AsyncMock())
        if all(p.success for p in pages):
            break
        assert all(p.metadata.get('render_turns', 0) == 0 for p in pages if not p.success)
    assert all(p.success for p in pages)
    assert set(received.values()) == set(rendered.values()) == {1}


@pytest.mark.asyncio
async def test_worker_respects_persisted_host_cooldown_without_new_requests():
    import time
    checkpoint = MemoryCheckpoint()
    await checkpoint.save('host_cooldown', {'until': time.time() + 30})
    fetch = AsyncMock()
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', checkpoint=checkpoint)
    fetcher._http = SimpleNamespace(fetch=fetch)
    pages = await fetcher.fetch_all('https://example.com', links(3), AsyncMock(), AsyncMock())
    fetch.assert_not_called()
    assert all(p.metadata['failure_kind'] == 'pending' for p in pages)


@pytest.mark.asyncio
async def test_browser_is_reused_between_batches():
    startups = []
    @asynccontextmanager
    async def crawler():
        startups.append(True)
        yield object()
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(page_batch_size=1), 'test', checkpoint=MemoryCheckpoint())
    fetcher.create_crawler = crawler
    async def fetch(source, url, *args):
        return HttpFetchOutcome(CrawledPage(url=url, success=False), requires_browser=True)
    async def render(crawler, source, url, *args):
        return CrawledPage(url=url, success=True)
    fetcher._http = SimpleNamespace(fetch=fetch)
    fetcher._browser = SimpleNamespace(render=render)
    pages = await fetcher.fetch_all('https://example.com', links(4), AsyncMock(), AsyncMock())
    assert all(p.success for p in pages) and len(startups) == 1


@pytest.mark.asyncio
async def test_continuation_cooldown_prevents_even_source_probe_on_redelivery():
    import time
    task_id, token, run_id = uuid4(), uuid4(), uuid4()
    checkpoint = MemoryCheckpoint()
    await checkpoint.save('host_cooldown', {'until': time.time() + 45})
    repository = SimpleNamespace(claim_task_explicit=AsyncMock(return_value=ClaimOutcome.claimed(token, datetime.now(UTC), 4)),
        get_by_id=AsyncMock(return_value=SimpleNamespace(run_id=run_id, source_id=uuid4(), execution_spec=object(), attempt_count=4)),
        defer_task=AsyncMock(return_value=True))
    run_repo = SimpleNamespace(get_by_id=AsyncMock(return_value=SimpleNamespace(id=run_id, monitoring_job_id=uuid4(), status=MonitoringRunStatus.RUNNING)))
    engine = SimpleNamespace(execute_crawl=AsyncMock())
    result = await ExecuteSourceTaskUseCase(repository, run_repo, engine, SimpleNamespace(), checkpoint_factory=lambda *args: checkpoint).execute(task_id)
    assert result['status'] == 'continuation_queued'
    engine.execute_crawl.assert_not_called()
    assert 40 <= repository.defer_task.call_args.kwargs['delay_seconds'] <= 45


@pytest.mark.asyncio
async def test_browser_checkpoint_fence_errors_are_not_swallowed():
    class Fenced(MemoryCheckpoint):
        async def save(self, key, value):
            if value.get('success'):
                raise CrawlCheckpointFenceLost('stale')
            await super().save(key, value)
    @asynccontextmanager
    async def crawler(): yield object()
    async def fetch(source, url, *args):
        return HttpFetchOutcome(CrawledPage(url=url, success=False), requires_browser=True)
    async def render(crawler, source, url, *args):
        return CrawledPage(url=url, success=True)
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', checkpoint=Fenced())
    fetcher.create_crawler = crawler
    fetcher._http = SimpleNamespace(fetch=fetch)
    fetcher._browser = SimpleNamespace(render=render)
    with pytest.raises(CrawlCheckpointFenceLost):
        await fetcher.fetch_all('https://example.com', links(1), AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_browser_failures_stop_per_url_without_repeating_http():
    checkpoint = MemoryCheckpoint()
    @asynccontextmanager
    async def crawler(): yield object()
    fetch = AsyncMock(side_effect=lambda source, url, *args: HttpFetchOutcome(CrawledPage(url=url, success=False), requires_browser=True))
    render = AsyncMock(side_effect=lambda crawler, source, url, *args: CrawledPage(url=url, success=False,
        metadata={'failure_kind': 'render_failed'}))
    for _ in range(5):
        fetcher = Crawl4AiPageFetcher(CrawlPolicy(), 'test', checkpoint=checkpoint)
        fetcher.create_crawler = crawler
        fetcher._http = SimpleNamespace(fetch=fetch)
        fetcher._browser = SimpleNamespace(render=render)
        pages = await fetcher.fetch_all('https://example.com', links(1), AsyncMock(), AsyncMock())
    assert fetch.await_count == 1 and render.await_count == 3
    assert not pages[0].success and not pages[0].metadata['retryable']


@pytest.mark.asyncio
async def test_later_failed_probe_keeps_previously_downloaded_pages():
    checkpoint = MemoryCheckpoint()
    selected = links(2)
    await checkpoint.save('plan', {'source_url': 'https://example.com', 'selected': [x.model_dump(mode='json') for x in selected],
                                  'discovered': [x.model_dump(mode='json') for x in selected]})
    await checkpoint.save('page:' + str(selected[0].url), CrawledPage(url=selected[0].url, success=True, html='article').model_dump(mode='json'))
    fetch = AsyncMock()
    result = await CrawlOrchestrator(CrawlPolicy(), checkpoint).crawl('https://example.com', 'topic', None, None,
        probe=AsyncMock(return_value=SourceProbe(requested_url='https://example.com', success=False, retryable=True)),
        discover=AsyncMock(), fetch_pages=fetch)
    assert result.diagnostics.reason_code == 'probe_failed'
    assert result.diagnostics.successful_pages == 1 and result.diagnostics.pending_pages == 1
    assert result.pages[0].html == 'article'
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_started_http_request_gets_full_attempt_despite_slice_boundary():
    async def fetch(source, url, *args):
        await asyncio.sleep(.025)
        return HttpFetchOutcome(CrawledPage(url=url, success=True))
    fetcher = Crawl4AiPageFetcher(CrawlPolicy(max_concurrency=1), 'test', batch_timeout_seconds=.01, checkpoint=MemoryCheckpoint())
    fetcher._http = SimpleNamespace(fetch=fetch)
    pages = await fetcher.fetch_all('https://example.com', links(2), AsyncMock(), AsyncMock())
    assert pages[0].success and pages[1].metadata['failure_kind'] == 'pending'
