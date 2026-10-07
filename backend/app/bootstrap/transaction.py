"""Transaction boundary used by command providers."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.shared.application.unit_of_work import UnitOfWork

T = TypeVar("T")


class TransactionRunner:
    """Commit one command or roll it back when it fails."""

    def __init__(self, unit_of_work: UnitOfWork) -> None:
        self._uow = unit_of_work

    async def run(self, operation: Callable[[], Awaitable[T]]) -> T:
        try:
            result = await operation()
            await self._uow.commit()
            return result
        except Exception:
            await self._uow.rollback()
            raise
