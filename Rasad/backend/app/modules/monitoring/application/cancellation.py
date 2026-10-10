"""Cancellation guard for monitoring job execution."""

from typing import Protocol
from uuid import UUID


class CancellationChecker(Protocol):
    async def is_cancelled(self, job_id: UUID) -> bool: ...


class CancellationGuard:
    """Check whether a monitoring job or run must stop."""

    def __init__(self, checker: CancellationChecker) -> None:
        self._checker = checker

    async def is_cancelled(self, job_id: UUID) -> bool:
        return await self._checker.is_cancelled(job_id)
