from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase

from app.modules.crawling.application import BuildCrawlRequest, CrawlSource, ProbeSource
from app.modules.crawling.domain import (
    CrawlOutcome,
    CrawlRequest,
    SourceProbeResult,
)


class FakeSourceCrawler:
    def __init__(self) -> None:
        self.request: CrawlRequest | None = None

    async def crawl(self, request: CrawlRequest) -> CrawlOutcome:
        self.request = request
        now = datetime.now(UTC)
        return CrawlOutcome(
            source_url=request.source_url,
            probe=SourceProbeResult(
                requested_url=request.source_url,
                final_url=request.source_url,
                success=True,
                http_status=200,
                response_time_ms=10,
                content_type="text/html",
                redirected=False,
                robots_allowed=True,
                reason=None,
            ),
            discovered_links=(),
            pages=(),
            started_at=now,
            finished_at=now,
        )

    async def probe(self, source_url: str) -> SourceProbeResult:
        return SourceProbeResult(
            requested_url=source_url,
            final_url=source_url,
            success=True,
            http_status=200,
            response_time_ms=10,
            content_type="text/html",
            redirected=False,
            robots_allowed=True,
            reason=None,
        )


class CrawlingApplicationTests(IsolatedAsyncioTestCase):
    async def test_crawl_source_passes_request_without_infrastructure_dependency(self) -> None:
        crawler = FakeSourceCrawler()
        request = BuildCrawlRequest().execute(
            source_url="https://example.com",
            topic="موضوع",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 2),
            policy={"max_pages": 3},
        )
        outcome = await CrawlSource(crawler).execute(request)
        self.assertIs(crawler.request, request)
        self.assertEqual(outcome.pages, ())
        self.assertEqual(request.policy, {"max_pages": 3})

    async def test_probe_source_returns_typed_result(self) -> None:
        result = await ProbeSource(FakeSourceCrawler()).execute("https://example.com")
        self.assertIsInstance(result, SourceProbeResult)
        self.assertEqual(result.http_status, 200)
