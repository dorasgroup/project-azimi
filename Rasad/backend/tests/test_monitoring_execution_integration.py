"""Golden integration tests for MonitoringRun + SourceTask execution in existing pipeline."""

import asyncio
from datetime import UTC, date, datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.modules.monitoring.application.cancellation import CancellationGuard
from app.modules.monitoring.application.progress import SourceProgressRecorder
from app.modules.monitoring.application.source_batch import SourceBatchProcessor
from app.modules.monitoring.application.types import (
    MonitoringExecutionContext,
    MonitoringSourceSnapshot,
    SourceProcessingResult,
)
from app.modules.monitoring.domain.execution import (
    MonitoringRun,
    MonitoringRunStatus,
    SourceTask,
    SourceTaskStatus,
)


class MonitoringExecutionIntegrationTests(IsolatedAsyncioTestCase):
    async def test_batch_processor_preserves_bounded_concurrency(self) -> None:
        """7 sources are processed in 5 + 2 bounded concurrency with accurate progress snapshots."""
        sources: list[MonitoringSourceSnapshot] = [
            {"id": uuid4(), "title": f"source-{i}", "base_url": f"https://s{i}.com", "crawl_config": {}}
            for i in range(1, 8)
        ]
        context = MonitoringExecutionContext(
            job_id=uuid4(),
            run_id=uuid4(),
            topic="سلامت",
            description="",
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 20),
            sources=sources,
        )

        active = 0
        maximum_active = 0
        release = asyncio.Event()

        async def mock_process_source(_ctx, _src):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            if maximum_active == 5:
                release.set()
            await release.wait()
            await asyncio.sleep(0)
            active -= 1
            return SourceProcessingResult(pages=2, documents=1)

        guard = MagicMock(spec=CancellationGuard)
        guard.is_cancelled = AsyncMock(return_value=False)
        recorder = MagicMock(spec=SourceProgressRecorder)
        recorder.record_crawl_progress = AsyncMock()
        processor_port = MagicMock()
        processor_port.process_source = mock_process_source

        batch_processor = SourceBatchProcessor(guard, recorder, processor_port, max_concurrency=5)
        progress = await batch_processor.process_sources(context)

        self.assertEqual(maximum_active, 5)
        self.assertEqual(progress.documents, 7)
        self.assertIsNone(progress.terminal_result)

    def test_run_completion_with_partial_source_failure(self) -> None:
        """A run with 1 failed task and 6 successful tasks terminates cleanly with correct counters."""
        run = MonitoringRun.create(monitoring_job_id=uuid4(), expected_sources=7)
        running = run.transition_to(MonitoringRunStatus.RUNNING)
        with_counters = running.with_counters(terminal_sources=7, successful_sources=6, failed_sources=1)
        completed = with_counters.transition_to(MonitoringRunStatus.COMPLETED)

        self.assertEqual(completed.status, MonitoringRunStatus.COMPLETED)
        self.assertEqual(completed.expected_sources, 7)
        self.assertEqual(completed.terminal_sources, 7)
        self.assertEqual(completed.successful_sources, 6)
        self.assertEqual(completed.failed_sources, 1)
