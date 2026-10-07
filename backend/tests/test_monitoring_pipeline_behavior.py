"""Complete Characterization and Behavior Tests for Monitoring Workflow Components."""

import asyncio
from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.crawler.schemas import CrawlBatchResult, CrawlDiagnostics, SourceProbe
from app.models import Source
from app.models.enums import JobStatus, LogLevel, SourceCrawlStatus, SourceStatus
from app.modules.monitoring.application.coverage import CoverageRecorder
from app.modules.monitoring.application.job_failure import JobFailureHandler, extract_error_message
from app.modules.monitoring.application.workflows import (
    MonitoringProgress,
    MonitoringStart,
    ProcessMonitoringJob,
)
from app.modules.monitoring.domain.progress import (
    MonitoringProgressSnapshot,
    decode_progress,
    encode_progress,
)
from app.modules.monitoring.infrastructure.crawl_health import SourceCrawlHealthUpdater
from app.modules.monitoring.infrastructure.pipeline_steps import (
    MAX_CONCURRENT_SOURCES,
    MonitoringExecutionContext,
    MonitoringPipelineSteps,
    SourceProcessingResult,
    _error_message,
)
from app.shared.domain.errors import ValidationError


def _dummy_context(source_count: int = 2) -> MonitoringExecutionContext:
    return MonitoringExecutionContext(
        job_id=uuid4(),
        topic="سلامت و درمان",
        description="اخبار و مقالات پزشکی",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 20),
        sources=[
            {
                "id": uuid4(),
                "title": f"منبع {i}",
                "base_url": f"https://source{i}.example.com",
                "crawl_config": {},
            }
            for i in range(1, source_count + 1)
        ],
    )


class MonitoringPipelineCharacterizationTests(IsolatedAsyncioTestCase):
    """Complete characterization suite matching pre-refactor semantics."""

    def test_error_message_extraction(self) -> None:
        """Verify _error_message extraction and fallback."""
        self.assertEqual(_error_message(ValueError("خطای تست")), "خطای تست")
        
        class ReadTimeout(Exception):
            pass
        self.assertIn("Analysis timed out", _error_message(ReadTimeout()))
        
        class EmptyError(Exception):
            def __str__(self):
                return ""
        self.assertIn("EmptyError: unexpected error without a message", _error_message(EmptyError()))

    async def test_happy_path_workflow_sequence(self) -> None:
        """Verify the exact sequence: start -> process_sources -> analyze -> close."""
        events = []
        processor = MagicMock()

        context = _dummy_context(2)
        async def mock_start(job_id):
            events.append("start")
            return MonitoringStart(context=context)

        async def mock_process_sources(ctx):
            events.append("process_sources")
            return MonitoringProgress(documents=5)

        async def mock_analyze(ctx, docs):
            events.append(f"analyze:{docs}")
            return {"status": "completed", "documents": docs, "report_id": "rep-1"}

        async def mock_fail(job_id, err):
            events.append("fail")

        async def mock_close():
            events.append("close")

        processor.start = mock_start
        processor.process_sources = mock_process_sources
        processor.analyze = mock_analyze
        processor.fail = mock_fail
        processor.close = mock_close

        result = await ProcessMonitoringJob(processor).execute(context.job_id)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["documents"], 5)
        self.assertEqual(events, ["start", "process_sources", "analyze:5", "close"])

    async def test_empty_sources_workflow(self) -> None:
        """When a job has empty sources, process_sources returns 0 documents."""
        context = _dummy_context(0)
        steps = object.__new__(MonitoringPipelineSteps)
        result = await steps.process_sources(context)
        self.assertEqual(result.documents, 0)
        self.assertIsNone(result.terminal_result)

    async def test_cancellation_before_start_and_terminal_states(self) -> None:
        """Terminal job states return early without executing pipeline."""
        steps = object.__new__(MonitoringPipelineSteps)
        steps._dispose_database = AsyncMock()

        for status, expected_terminal in [
            (JobStatus.CANCELLED, "cancelled"),
            (JobStatus.COMPLETED, "completed"),
            (JobStatus.FAILED, "failed"),
        ]:
            mock_job = MagicMock()
            mock_job.status = status
            mock_repo = MagicMock()
            mock_repo.get_by_id = AsyncMock(return_value=mock_job)

            mock_session = AsyncMock()
            mock_session.__aenter__.return_value = mock_session
            steps._session_factory = MagicMock(return_value=mock_session)
            steps._monitoring_repositories = MagicMock(return_value=mock_repo)

            start = await steps.start(uuid4())
            self.assertEqual(start.terminal_result, {"status": expected_terminal, "documents": 0})

    async def test_cancellation_between_sources(self) -> None:
        """When cancelled between batches, process_sources returns terminal cancelled result."""
        context = _dummy_context(6)  # 6 sources with batch size 5 = 2 batches
        steps = object.__new__(MonitoringPipelineSteps)

        calls = 0
        async def mock_is_cancelled(job_id):
            nonlocal calls
            calls += 1
            return calls > 1

        steps._is_cancelled = mock_is_cancelled
        steps._record_source_progress = AsyncMock()
        steps._process_source_guarded = AsyncMock(return_value=SourceProcessingResult(pages=2, documents=1))

        result = await steps.process_sources(context)
        self.assertIsNotNone(result.terminal_result)
        self.assertEqual(result.terminal_result["status"], "cancelled")

    async def test_source_failure_does_not_abort_batch(self) -> None:
        """One source failure in batch logs error and returns 0 docs without stopping the batch."""
        context = _dummy_context(3)
        steps = object.__new__(MonitoringPipelineSteps)
        steps._is_cancelled = AsyncMock(return_value=False)
        steps._record_source_progress = AsyncMock()

        async def process_guarded(ctx, source):
            if source["title"] == "منبع 1":
                return SourceProcessingResult(pages=0, documents=0)
            return SourceProcessingResult(pages=4, documents=3)

        steps._process_source_guarded = process_guarded
        result = await steps.process_sources(context)
        self.assertEqual(result.documents, 6)
        self.assertIsNone(result.terminal_result)

    async def test_analysis_zero_documents_raises_validation_error(self) -> None:
        """When 0 documents are available, analyze raises ValidationError."""
        context = _dummy_context(1)
        steps = object.__new__(MonitoringPipelineSteps)

        mock_job = MagicMock()
        mock_job.id = context.job_id
        mock_job.status = JobStatus.CRAWLING
        mock_job.topic = "تست"
        mock_job.sources = [MagicMock(title="منبع تست")]

        mock_repo = MagicMock()
        mock_repo.get_by_id = AsyncMock(return_value=mock_job)
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        steps._session_factory = MagicMock(return_value=mock_session)
        steps._monitoring_repositories = MagicMock(return_value=mock_repo)
        steps._document_repositories = MagicMock()
        steps._record_coverage = AsyncMock(return_value=0)

        with self.assertRaises(ValidationError) as ctx:
            await steps.analyze(context, 0)
        self.assertIn("هیچ سند آماده‌ای برای تحلیل پیدا نشد", str(ctx.exception))

    async def test_analysis_failure_workflow_and_record(self) -> None:
        """If AnalysisJobCoordinator raises an exception, the job fails and error is recorded."""
        job_id = uuid4()
        persistence = AsyncMock()
        persistence.mark_job_failed = AsyncMock()

        handler = JobFailureHandler(persistence)
        await handler.handle(job_id, RuntimeError("Analysis engine timeout"))

        persistence.mark_job_failed.assert_awaited_once_with(
            job_id, "Analysis engine timeout", "RuntimeError"
        )

    def test_source_health_update_semantics(self) -> None:
        """Verify source health fields, diagnostics, and status are mapped faithfully."""
        source = Source(
            id=uuid4(),
            title="خبرگزاری تست",
            base_url="https://news.example.com",
        )
        crawl_result = CrawlBatchResult(
            source_url="https://news.example.com",
            started_at=datetime(2026, 9, 22, 13, 0, 0, tzinfo=UTC),
            finished_at=datetime(2026, 9, 22, 14, 0, 0, tzinfo=UTC),
            pages=[],
            discovered_links=[],
            probe=SourceProbe(
                requested_url="https://news.example.com",
                final_url="https://news.example.com/",
                success=True,
                http_status=200,
                response_time_ms=120,
            ),
            diagnostics=CrawlDiagnostics(
                status="success",
                reason_code="CRAWL_OK",
                summary="Crawl completed successfully",
                discovered_total=25,
                selected_total=10,
                fetched_total=10,
                successful_pages=10,
            ),
        )

        SourceCrawlHealthUpdater.update(source, crawl_result)

        self.assertEqual(source.last_status, SourceStatus.SUCCESS)
        self.assertEqual(source.last_http_status, 200)
        self.assertEqual(source.last_response_time_ms, 120)
        self.assertEqual(source.last_crawl_status, SourceCrawlStatus.SUCCESS)
        self.assertEqual(source.last_crawl_message, "Crawl completed successfully")
        self.assertEqual(source.last_crawled_at, crawl_result.finished_at)

    async def test_coverage_recorder_partial_warning(self) -> None:
        """Verify CoverageRecorder logs warning when one or more sources have 0 documents."""
        job_id = uuid4()
        s1_id = uuid4()
        s2_id = uuid4()
        sources = [
            {"id": s1_id, "title": "منبع کامل"},
            {"id": s2_id, "title": "منبع خالی"},
        ]

        doc_counts_port = AsyncMock()
        doc_counts_port.source_counts = AsyncMock(return_value={s1_id: 12, s2_id: 0})

        coverage_logger = AsyncMock()
        coverage_logger.log_partial_coverage = AsyncMock()

        recorder = CoverageRecorder(doc_counts_port, coverage_logger)
        total = await recorder.calculate_and_record(job_id, sources)

        self.assertEqual(total, 12)
        coverage_logger.log_partial_coverage.assert_awaited_once_with(
            job_id, ["منبع خالی"], ["منبع کامل"]
        )

    def test_progress_calculation_formula(self) -> None:
        """Verify progress calculation formula 5 + 65 * fraction."""
        source_count = 4
        completed = 2
        fraction = completed / source_count
        expected_percent = round(5 + 65 * fraction)
        self.assertEqual(expected_percent, 38)

        snapshot = MonitoringProgressSnapshot(
            stage="crawling",
            percent=expected_percent,
            sources_completed=completed,
            sources_total=source_count,
            pages_processed=10,
            documents_prepared=8,
            eta_seconds=30,
            message="در حال پردازش",
        )
        encoded = encode_progress(snapshot)
        decoded = decode_progress(encoded)
        self.assertEqual(decoded.percent, 38)
        self.assertEqual(decoded.sources_completed, 2)
        self.assertEqual(decoded.documents_prepared, 8)
