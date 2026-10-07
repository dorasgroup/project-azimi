"""Transaction boundary required by application use cases."""

from typing import Protocol, Self


class UnitOfWork(Protocol):
    """Minimal transaction contract; implementations belong to infrastructure."""

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
