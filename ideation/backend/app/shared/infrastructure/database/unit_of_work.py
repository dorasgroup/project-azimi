from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from app.shared.application.unit_of_work import IUnitOfWork
from app.modules.users.infrastructure.repositories import PostgresUserRepository

class PostgresUnitOfWork(IUnitOfWork):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self.session_factory = session_factory
        self.session = None

    async def __aenter__(self):
        self.session = self.session_factory()
        self.users = PostgresUserRepository(self.session)
        return super().__aenter__()

    async def __aexit__(self, exc_type, exc_val, traceback):
        if exc_type:
            await self.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()
