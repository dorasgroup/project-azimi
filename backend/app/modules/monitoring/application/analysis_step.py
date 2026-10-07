"""Analysis invocation step for monitoring jobs."""

from typing import Protocol
from uuid import UUID

from app.modules.monitoring.application.coverage import CoverageRecorder
from app.modules.monitoring.application.progress import SourceProgressRecorder
from app.modules.monitoring.application.types import MonitoringExecutionContext
from app.shared.domain.errors import ValidationError


class AnalysisCoordinatorPort(Protocol):
    async def analyze_job(self, job_id: UUID) -> object: ...


class AnalysisStatusPort(Protocol):
    async def transition_to_analyzing(self, job_id: UUID) -> bool: ...
    async def log_analysis_started(self, job_id: UUID, documents: int) -> None: ...
    async def log_job_completed(self, job_id: UUID, documents: int, report_id: str) -> None: ...
    async def is_cancelled(self, job_id: UUID) -> bool: ...
    async def is_missing(self, job_id: UUID) -> bool: ...


class AnalysisStep:
    """Validate documents and coordinate analysis execution."""

    def __init__(
        self,
        coverage_recorder: CoverageRecorder,
        progress_recorder: SourceProgressRecorder,
        status_port: AnalysisStatusPort,
        coordinator: AnalysisCoordinatorPort,
    ) -> None:
        self._coverage_recorder = coverage_recorder
        self._progress_recorder = progress_recorder
        self._status_port = status_port
        self._coordinator = coordinator

    async def run(
        self, context: MonitoringExecutionContext, documents: int
    ) -> dict[str, object]:
        if await self._status_port.is_missing(context.job_id):
            return {"status": "missing", "documents": documents}
        if await self._status_port.is_cancelled(context.job_id):
            return {"status": "cancelled", "documents": documents}

        # Calculate and record coverage
        total_documents = await self._coverage_recorder.calculate_and_record(
            context.job_id, context.sources
        )

        if total_documents == 0:
            source_titles = "، ".join(s["title"] for s in context.sources) or "بدون منبع"
            raise ValidationError(
                "هیچ سند آماده‌ای برای تحلیل پیدا نشد. "
                f"موضوع «{context.topic}» در منابع انتخاب‌شده ({source_titles}) محتوای قابل تحلیل تولید نکرد."
            )

        # Record progress and transition
        await self._progress_recorder.record_analysis_progress(
            context.job_id,
            sources_total=len(context.sources),
            sources_completed=len(context.sources),
            pages_processed=total_documents,
            documents_prepared=total_documents,
        )
        await self._status_port.transition_to_analyzing(context.job_id)
        await self._status_port.log_analysis_started(context.job_id, total_documents)

        # Invoke coordinator
        report = await self._coordinator.analyze_job(context.job_id)
        report_id = str(getattr(report, "id", report))

        await self._status_port.log_job_completed(context.job_id, total_documents, report_id)

        return {
            "status": "completed",
            "documents": total_documents,
            "report_id": report_id,
        }
