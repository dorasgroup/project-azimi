"""Framework-independent monitoring workflow orchestration."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.shared.domain.errors import ValidationError


class MonitoringContext(Protocol):
    job_id: UUID


@dataclass(frozen=True)
class MonitoringStart:
    context: MonitoringContext | None = None
    terminal_result: dict[str, object] | None = None
    resume_analysis: bool = False


@dataclass(frozen=True)
class MonitoringProgress:
    documents: int
    terminal_result: dict[str, object] | None = None


class MonitoringJobProcessor(Protocol):
    async def start(self, job_id: UUID) -> MonitoringStart: ...

    async def process_sources(self, context: MonitoringContext) -> MonitoringProgress: ...

    async def analyze(self, context: MonitoringContext, documents: int) -> dict[str, object]: ...

    async def fail(self, job_id: UUID, error: Exception) -> None: ...

    async def close(self) -> None: ...


class ProcessMonitoringJob:
    """Coordinate durable monitoring steps without framework knowledge."""

    def __init__(self, processor: MonitoringJobProcessor) -> None:
        self._processor = processor

    async def execute(self, job_id: UUID) -> dict[str, object]:
        try:
            start = await self._processor.start(job_id)
            if start.terminal_result is not None:
                return start.terminal_result
            if start.context is None:
                raise ValidationError("Monitoring processor returned no context")
            if start.resume_analysis:
                return await self._processor.analyze(start.context, 0)
            progress = await self._processor.process_sources(start.context)
            if progress.terminal_result is not None:
                return progress.terminal_result
            return await self._processor.analyze(start.context, progress.documents)
        except Exception as exc:
            await self._processor.fail(job_id, exc)
            raise
        finally:
            await self._processor.close()
