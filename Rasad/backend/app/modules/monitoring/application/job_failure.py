"""Job failure handler for monitoring workflows."""

from typing import Protocol
from uuid import UUID


class JobFailurePersistencePort(Protocol):
    async def mark_job_failed(
        self, job_id: UUID, error_message: str, error_type: str
    ) -> None: ...


def extract_error_message(exc: Exception) -> str:
    message = str(exc).strip()
    if message:
        return message
    if exc.__class__.__name__ == "ReadTimeout":
        return "Analysis timed out. Check the active model connection and retry the monitoring job."
    return f"{exc.__class__.__name__}: unexpected error without a message"


class JobFailureHandler:
    """Transition monitoring job to failed and record error diagnostic log."""

    def __init__(self, persistence: JobFailurePersistencePort) -> None:
        self._persistence = persistence

    async def handle(self, job_id: UUID, error: Exception) -> None:
        readable_error = extract_error_message(error)
        await self._persistence.mark_job_failed(
            job_id, readable_error, error.__class__.__name__
        )
