from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase

from app.bootstrap.crawler_worker import crawl_source, probe_source
from app.bootstrap.processing_worker import crawl_and_prepare_source, prepare_pages
from app.modules.crawling.domain import (
    CrawledPageRecord,
    CrawlOutcome,
    DiscoveredLinkRecord,
    SourceProbeResult,
)
from app.modules.documents.domain import (
    DocumentPreparationOutcome,
    PreparedDocumentRecord,
)


def _probe() -> SourceProbeResult:
    return SourceProbeResult(
        requested_url="https://example.com",
        final_url="https://example.com/",
        success=True,
        http_status=200,
        response_time_ms=12,
        content_type="text/html",
        redirected=True,
        robots_allowed=True,
        reason=None,
    )


def _page() -> CrawledPageRecord:
    return CrawledPageRecord(
        url="https://example.com/article",
        final_url=None,
        success=True,
        status_code=200,
        title="Example",
        html="<p>topic text</p>",
        markdown="topic text",
        content_hash="hash",
        cache_status="miss",
        error=None,
        metadata={"source": "test"},
    )


class FakeCrawler:
    def __init__(self) -> None:
        self.request = None

    async def probe(self, source_url):
        return _probe()

    async def crawl(self, request):
        self.request = request
        now = datetime(2026, 7, 22, tzinfo=UTC)
        return CrawlOutcome(
            source_url=request.source_url,
            probe=_probe(),
            discovered_links=(
                DiscoveredLinkRecord(
                    url="https://example.com/article",
                    title="Example",
                    discovered_via="html",
                    published_at=now,
                    topic_matches=("topic",),
                ),
            ),
            pages=(_page(),),
            started_at=now,
            finished_at=now,
            errors=("internal-only",),
        )


class FakeDocumentProcessor:
    def __init__(self) -> None:
        self.request = None

    def prepare(self, request):
        self.request = request
        return DocumentPreparationOutcome(
            documents=(
                PreparedDocumentRecord(
                    url=request.pages[0].url,
                    canonical_url=request.pages[0].url,
                    title="Example",
                    clean_text="topic text",
                    markdown="topic text",
                    author=None,
                    source_name=request.source_name,
                    published_at=None,
                    published_at_jalali=None,
                    language="en",
                    lead_image_url=None,
                    matched_score=1.0,
                    matched_terms=("topic",),
                    content_hash="hash",
                    metadata={"source": "test"},
                ),
            ),
            rejected=(),
            duplicate_count=0,
        )


class PhaseSixCrawlerCompositionTests(IsolatedAsyncioTestCase):
    async def test_probe_delegates_to_application_use_case(self) -> None:
        result = await probe_source(FakeCrawler(), "https://example.com")
        self.assertEqual(result["requested_url"], "https://example.com")
        self.assertTrue(result["success"])

    async def test_crawl_preserves_public_task_contract(self) -> None:
        crawler = FakeCrawler()
        result = await crawl_source(
            crawler,
            "https://example.com",
            "topic",
            {"max_pages": 2},
            "2026-07-01",
            "2026-07-22",
        )

        self.assertEqual(
            set(result),
            {"source_url", "probe", "discovered_links", "pages", "started_at", "finished_at"},
        )
        self.assertNotIn("errors", result)
        self.assertEqual(crawler.request.policy, {"max_pages": 2})
        self.assertEqual(result["discovered_links"][0]["published_at"], "2026-07-22T00:00:00+00:00")

    async def test_combined_worker_composes_crawl_and_prepare_use_cases(self) -> None:
        crawler = FakeCrawler()
        processor = FakeDocumentProcessor()
        result = await crawl_and_prepare_source(
            crawler,
            processor,
            "https://example.com",
            "topic",
            "Example Source",
        )

        self.assertEqual(result["documents"][0]["source_name"], "Example Source")
        self.assertEqual(processor.request.pages[0].url, "https://example.com/article")


class PhaseSixProcessingCompositionTests(TestCase):
    def test_prepare_pages_delegates_to_application_use_case(self) -> None:
        processor = FakeDocumentProcessor()
        result = prepare_pages(
            processor,
            [
                {
                    "url": "https://example.com/article",
                    "success": True,
                    "markdown": "topic text",
                    "metadata": {"source": "test"},
                }
            ],
            "topic",
            "Example Source",
            {"min_text_length": 1},
        )

        self.assertEqual(result["duplicate_count"], 0)
        self.assertEqual(processor.request.policy, {"min_text_length": 1})
        self.assertEqual(processor.request.source_name, "Example Source")
