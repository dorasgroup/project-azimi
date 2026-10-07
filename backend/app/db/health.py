from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings

engine: AsyncEngine | None = None


async def open_database() -> None:
    global engine
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)


async def close_database() -> None:
    global engine
    if engine is not None:
        await engine.dispose()
        engine = None


async def database_is_ready() -> bool:
    if engine is None:
        return False

    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
