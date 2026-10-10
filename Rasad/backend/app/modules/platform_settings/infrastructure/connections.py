"""Focused infrastructure adapters for system connection operations."""

import time

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.platform_settings.infrastructure.provider_commands import (
    activate_provider,
    delete_provider_settings,
    save_provider_settings,
)
from app.modules.platform_settings.infrastructure.provider_queries import (
    get_active_model_provider,
    get_configured_providers,
)
from app.schemas.system_connection import ConnectionCheck


def _result(name: str, state: str, message: str, started: float) -> ConnectionCheck:
    return ConnectionCheck(
        name=name,
        status=state,
        message=message,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class DatabaseConnectionProbe:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def check(self) -> ConnectionCheck:
        started = time.perf_counter()
        try:
            async with self._session_factory() as session:
                await session.execute(text("select 1"))
            return _result("database", "success", "Database connection is ready", started)
        except Exception as exc:
            return _result("database", "failed", str(exc), started)


class RedisConnectionProbe:
    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url

    async def check(self) -> ConnectionCheck:
        started = time.perf_counter()
        client = Redis.from_url(self._redis_url)
        try:
            await client.ping()
            return _result("redis", "success", "Redis connection is ready", started)
        except Exception as exc:
            return _result("redis", "failed", str(exc), started)
        finally:
            await client.aclose()


class ProviderSettingsQueryAdapter:
    async def list_configured(self) -> list[dict[str, object]]:
        return await get_configured_providers()

class ProviderSettingsCommandAdapter:
    async def update(
        self,
        provider_id: str,
        api_key: str | None,
        model: str | None,
        embedding_model: str | None,
    ) -> dict[str, object]:
        return await save_provider_settings(provider_id, api_key, model, embedding_model)

    async def activate(self, provider_id: str) -> list[dict[str, object]]:
        return await activate_provider(provider_id)

    async def delete(self, provider_id: str) -> list[dict[str, object]]:
        return await delete_provider_settings(provider_id)


class ModelProviderConnectionProbe:
    async def check(self) -> ConnectionCheck:
        started = time.perf_counter()
        try:
            provider = get_active_model_provider()
            return _result(
                "model_provider",
                "success",
                f"Native analysis provider {provider.provider_id}/{provider.model} is configured",
                started,
            )
        except Exception as exc:
            return _result("model_provider", "failed", str(exc), started)
