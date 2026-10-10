"""Startup step for monitoring workflow execution."""

from typing import Protocol
from uuid import UUID

from app.modules.monitoring.application.types import MonitoringExecutionContext
from app.modules.monitoring.application.workflows import MonitoringStart


class StartupPersistencePort(Protocol):
    async def load_and_start(
        self, job_id: UUID
    ) -> tuple[str | None, MonitoringExecutionContext | None, bool]:
        """
        Returns:
            (terminal_status, context, resume_analysis)
        """
        ...


class JobStartupStep:
    """Validate initial state, transition to CRAWLING, and construct execution context."""

    def __init__(self, persistence: StartupPersistencePort) -> None:
        self._persistence = persistence

    async def run(self, job_id: UUID) -> MonitoringStart:
        terminal_status, context, resume_analysis = await self._persistence.load_and_start(job_id)
        if terminal_status is not None:
            return MonitoringStart(terminal_result={"status": terminal_status, "documents": 0})
        return MonitoringStart(context=context, resume_analysis=resume_analysis)
