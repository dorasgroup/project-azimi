"""Database readiness adapter."""

from app.db.health import database_is_ready


class DatabaseReadinessProbe:
    async def ready(self) -> bool:
        return await database_is_ready()
