from abc import ABC, abstractmethod
from app.modules.users.application.repositories import IUserRepository

class IUnitOfWork(ABC):
    users: IUserRepository

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, traceback):
        await self.rollback()

    @abstractmethod
    async def commit(self):
        pass

    @abstractmethod
    async def rollback(self):
        pass
