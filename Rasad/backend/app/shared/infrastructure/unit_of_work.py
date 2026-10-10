"""SQLAlchemy implementation of the shared transaction contract."""

from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession


class SqlAlchemyUnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if exc_type is None:
            await self.commit()
        else:
            await self.rollback()

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()
