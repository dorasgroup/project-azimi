from fastapi import Depends
from app.shared.infrastructure.database.session import AsyncSessionLocal
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.infrastructure.database.unit_of_work import PostgresUnitOfWork

def get_uow() -> IUnitOfWork:
    return PostgresUnitOfWork(session_factory=AsyncSessionLocal)
