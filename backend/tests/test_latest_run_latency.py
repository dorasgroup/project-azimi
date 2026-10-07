"""Regression coverage for the real 30-source failure and bounded tails."""
import asyncio
import time
from datetime import date
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace
from contextlib import asynccontextmanager
import httpx
from app.crawler.schemas import CrawlPolicy, DiscoveredLink, CrawledPage
from app.modules.monitoring.domain.crawl_policy import effective_crawl_policy
from app.modules.crawling.infrastructure.link_discovery import LinkDiscoveryService, discovery_trace
from app.modules.crawling.infrastructure.html_discovery import HtmlDiscoveryProvider
from app.modules.analysis.infrastructure.model_gateway import OpenAICompatibleModelGateway, ModelGatewayError
from app.modules.analysis.domain import ModelGenerationRequest
from app.modules.platform_settings.domain import ModelProviderRuntime
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
from app.modules.analysis.domain.source_analysis import SourceAnalysisUnavailableError, NoRelevantEvidenceError
from app.modules.crawling.infrastructure.page_fetcher import Crawl4AiPageFetcher
from app.modules.crawling.infrastructure.http_page_fetcher import HttpFetchOutcome
from app.modules.analysis.infrastructure.run_aggregation_repository import SqlAlchemyRunAggregationRepository

class LatencyPolicyTests(TestCase):
    def test_thirty_source_stages_leave_persistence_slack(self):
        p=effective_crawl_policy({}, date_from=date(2026,9,23), date_to=date(2026,10,2), source_count=30)
        self.assertEqual(p.max_pages,25)
        self.assertLessEqual(p.probe_timeout_seconds+p.discovery_timeout_seconds+p.historical_timeout_seconds+p.fetch_batch_timeout_seconds,100)
        self.assertTrue(p.respect_robots_txt)

class DeadlineTests(IsolatedAsyncioTestCase):
    async def test_compatibility_retries_share_deadline(self):
        calls=[]
        async def handle(req):
            calls.append(req)
            await asyncio.sleep(.03)
            return httpx.Response(400,json={'error':'unsupported response_format json_schema'})
        g=OpenAICompatibleModelGateway(ModelProviderRuntime('test','model','https://model.test/v1','secret'),timeout_seconds=.05,transport=httpx.MockTransport(handle))
        start=time.monotonic()
        with self.assertRaises(ModelGatewayError) as caught:
            await g.generate_json(ModelGenerationRequest('system','user',output_schema={'type':'object'}))
        self.assertTrue(caught.exception.retryable)
        self.assertLess(time.monotonic()-start,.2)
        self.assertEqual(len(calls),2)

    async def test_discovery_preserves_links_before_slow_tail(self):
        policy=CrawlPolicy(discovery_timeout_seconds=.05,historical_discovery_enabled=False)
        service=LinkDiscoveryService(policy)
        link=DiscoveredLink(url='https://example.com/news/one',title='Iran US talks',discovered_via='html')
        cancelled=asyncio.Event()
        async def fetch(url,**kw):
            if url.endswith('/fast'):
                return httpx.Response(200,request=httpx.Request('GET',url))
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        async def html(self,source,topic,seeds,get):
            await get('https://example.com/fast')
            found=[link]
            try:
                await get('https://example.com/slow')
            except TimeoutError:
                return found
        with patch.object(HtmlDiscoveryProvider,'discover',html), patch('app.modules.crawling.infrastructure.link_discovery.FeedDiscoveryProvider.discover',new=AsyncMock(return_value=[])), patch('app.modules.crawling.infrastructure.link_discovery.SitemapDiscoveryProvider.robots_sitemaps',new=AsyncMock(return_value=[])), patch('app.modules.crawling.infrastructure.link_discovery.SitemapDiscoveryProvider.discover',new=AsyncMock(return_value=[])):
            result=await service.discover('https://example.com','Iran US talks',fetch)
        self.assertEqual([str(x.url) for x in result],[str(link.url)])
        self.assertTrue(cancelled.is_set())
        self.assertIn('local',discovery_trace(service)['skipped']['time_budget'])

    async def test_analysis_failure_is_not_semantic_no_match(self):
        a=RunAggregatorAdapter()
        with self.assertRaises(SourceAnalysisUnavailableError):
            await a.aggregate({'prompt_version':'native-source-v2'},[],{'analysis_failed':7})
        with self.assertRaises(NoRelevantEvidenceError):
            await a.aggregate({'prompt_version':'native-source-v2'},[],{'analysis_failed':0})

    async def test_analysis_failure_with_only_empty_completed_results(self):
        with self.assertRaises(SourceAnalysisUnavailableError):
            await RunAggregatorAdapter().aggregate({'prompt_version':'native-source-v2'},[{'source_id':'source','summary':'No documents','findings':[],'evidence_map':{}}],{'analysis_failed':7})

    async def test_durable_analysis_counters_reach_aggregator(self):
        run=SimpleNamespace(expected_sources=30,successful_sources=24,failed_sources=6,cancelled_sources=0,monitoring_job_id='job')
        tracker=SimpleNamespace(analysis_successful_count=17,analysis_failed_count=7,analysis_cancelled_count=0)
        @asynccontextmanager
        async def session():
            yield SimpleNamespace(get=AsyncMock(return_value=run),scalar=AsyncMock(return_value=tracker),scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
        coverage=await SqlAlchemyRunAggregationRepository(session).get_run_coverage('run')
        self.assertEqual(coverage['analysis_failed'],7)
        self.assertEqual(coverage['analysis_successful'],17)

    async def test_browser_startup_timeout_keeps_usable_http_page(self):
        fetcher=Crawl4AiPageFetcher(CrawlPolicy(), 'test',batch_timeout_seconds=.03)
        page=CrawledPage(url='https://example.com/news/one',success=True,html='<html>usable</html>')
        fetcher._http=SimpleNamespace(fetch=AsyncMock(return_value=HttpFetchOutcome(page,requires_browser=True)))
        @asynccontextmanager
        async def browser():
            await asyncio.sleep(10)
            yield None
        with patch.object(fetcher,'create_crawler',browser):
            pages=await fetcher.fetch_all('https://example.com',[DiscoveredLink(url=page.url,discovered_via='html')],AsyncMock(),AsyncMock())
        self.assertTrue(pages[0].success)
