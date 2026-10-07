import asyncio
from datetime import date
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from uuid import uuid4

from app.crawler.discovery import SitemapReference
from app.modules.crawling.infrastructure.sitemap_discovery import SitemapDiscoveryProvider
from app.modules.monitoring.domain.crawl_policy import effective_crawl_policy
from app.modules.monitoring.domain.progress import (
    MonitoringProgressSnapshot,
    decode_progress,
    encode_progress,
)
from app.modules.monitoring.infrastructure.pipeline_steps import (
    MAX_CONCURRENT_SOURCES,
    MonitoringExecutionContext,
    MonitoringPipelineSteps,
    SourceProcessingResult,
)


def _context(source_count: int) -> MonitoringExecutionContext:
    return MonitoringExecutionContext(
        job_id=uuid4(),
        topic="topic",
        description=None,
        date_from=date(2026, 8, 23),
        date_to=date(2026, 9, 19),
        sources=[
            {
                "id": uuid4(),
                "title": f"source-{index}",
                "base_url": f"https://source-{index}.example",
                "crawl_config": {},
            }
            for index in range(source_count)
        ],
    )


class CrawlPolicyThroughputTests(IsolatedAsyncioTestCase):
    def test_short_range_limits_scale_with_source_count(self) -> None:
        expected = {
            3: (80, 5000),
            4: (50, 3500),
            10: (30, 2500),
            20: (25, 1500),
            30: (25, 1500),
        }

        for source_count, (max_pages, max_discovered_urls) in expected.items():
            with self.subTest(source_count=source_count):
                policy = effective_crawl_policy(
                    {},
                    date_from=date(2026, 8, 23),
                    date_to=date(2026, 9, 19),
                    source_count=source_count,
                )
                self.assertEqual(policy.max_pages, max_pages)
                self.assertEqual(policy.max_discovered_urls, max_discovered_urls)
                self.assertLessEqual(policy.max_sitemaps, 60)

    def test_explicit_lower_discovery_limit_is_preserved(self) -> None:
        policy = effective_crawl_policy(
            {"crawl_policy": {"max_discovered_urls": 800}},
            date_from=date(2026, 8, 23),
            date_to=date(2026, 9, 19),
            source_count=30,
        )

        self.assertEqual(policy.max_discovered_urls, 800)
        self.assertEqual(policy.max_sitemaps, 24)

    def test_long_range_keeps_date_coverage_minimum(self) -> None:
        policy = effective_crawl_policy(
            {},
            date_from=date(2025, 9, 20),
            date_to=date(2026, 9, 19),
            source_count=30,
        )

        self.assertEqual(policy.max_pages, 100)

    def test_sitemap_branches_outside_monitoring_year_are_rejected(self) -> None:
        self.assertFalse(
            SitemapDiscoveryProvider._reference_matches_range(
                SitemapReference(url="https://example.com/fa/2013/11/sitemap.xml"),
                date_from=date(2026, 8, 23),
                date_to=date(2026, 9, 21),
            )
        )
        self.assertTrue(
            SitemapDiscoveryProvider._reference_matches_range(
                SitemapReference(url="https://example.com/fa/2026/09/sitemap.xml"),
                date_from=date(2026, 8, 23),
                date_to=date(2026, 9, 21),
            )
        )


class MonitoringSourceConcurrencyTests(IsolatedAsyncioTestCase):
    async def test_sources_run_in_bounded_batches(self) -> None:
        steps = object.__new__(MonitoringPipelineSteps)
        active = 0
        maximum_active = 0
        release = asyncio.Event()

        async def process_source(_context, _source):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            if maximum_active == MAX_CONCURRENT_SOURCES:
                release.set()
            await release.wait()
            await asyncio.sleep(0)
            active -= 1
            return 1

        steps._is_cancelled = AsyncMock(return_value=False)
        steps._record_source_progress = AsyncMock()
        steps._process_source_guarded = process_source

        result = await steps.process_sources(_context(7))

        self.assertEqual(result.documents, 7)
        self.assertIsNone(result.terminal_result)
        self.assertEqual(maximum_active, MAX_CONCURRENT_SOURCES)
        self.assertEqual(steps._record_source_progress.await_count, 4)
        final_progress = steps._record_source_progress.await_args_list[-1].kwargs
        self.assertEqual(final_progress["sources_completed"], 7)
        self.assertEqual(final_progress["source_count"], 7)
        self.assertEqual(final_progress["documents_prepared"], 7)
        self.assertEqual(final_progress["eta_seconds"], 0)

    async def test_one_source_failure_does_not_stop_the_batch(self) -> None:
        steps = object.__new__(MonitoringPipelineSteps)
        processed: list[str] = []

        async def process_source(_context, source):
            processed.append(source["title"])
            return 0 if source["title"] == "source-1" else 1

        steps._is_cancelled = AsyncMock(return_value=False)
        steps._record_source_progress = AsyncMock()
        steps._process_source_guarded = process_source

        result = await steps.process_sources(_context(4))

        self.assertEqual(result.documents, 3)
        self.assertEqual(len(processed), 4)
        self.assertIsNone(result.terminal_result)

    def test_progress_payload_round_trip_is_bounded_and_structured(self) -> None:
        encoded = encode_progress(
            MonitoringProgressSnapshot(
                stage="crawling",
                percent=42,
                sources_completed=3,
                sources_total=7,
                pages_processed=18,
                documents_prepared=9,
                eta_seconds=75,
                message="در حال خزش",
            )
        )

        decoded = decode_progress(encoded)

        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.percent, 42)
        self.assertEqual(decoded.pages_processed, 18)
        self.assertEqual(decoded.eta_seconds, 75)
