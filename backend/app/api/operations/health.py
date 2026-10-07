"""Health-check transport operation over an injected readiness probe."""

from typing import Protocol

from app.schemas.health import HealthResponse


class ReadinessProbe(Protocol):
    async def ready(self) -> bool: ...


class HealthOperations:
    def __init__(self, database: ReadinessProbe) -> None:
        self._database = database

    async def read(self) -> HealthResponse:
        ready = await self._database.ready()
        return HealthResponse(status="ok" if ready else "degraded", service="api", database=ready)
