"""System-connection transport operations over injected ports."""

from typing import Protocol

from app.schemas.system_connection import ConnectionCheck, ConnectionsStatus


class ConnectionProbe(Protocol):
    async def check(self) -> ConnectionCheck: ...


class ProviderSettingsReader(Protocol):
    async def list_configured(self) -> list[dict[str, object]]: ...


class ProviderSettingsWriter(Protocol):
    async def update(
        self,
        provider_id: str,
        api_key: str | None,
        model: str | None,
        embedding_model: str | None,
    ) -> dict[str, object]: ...

    async def activate(self, provider_id: str) -> list[dict[str, object]]: ...

    async def delete(self, provider_id: str) -> list[dict[str, object]]: ...


class ConnectionHealthOperations:
    def __init__(
        self,
        database: ConnectionProbe,
        redis: ConnectionProbe,
        model_provider: ConnectionProbe,
        providers: ProviderSettingsReader,
    ) -> None:
        self._database = database
        self._redis = redis
        self._model_provider = model_provider
        self._providers = providers

    async def summary(self) -> ConnectionsStatus:
        return ConnectionsStatus(
            providers=await self._providers.list_configured(),
            checks=[
                await self._database.check(),
                await self._redis.check(),
                await self._model_provider.check(),
            ],
        )

    async def database(self) -> ConnectionCheck:
        return await self._database.check()

    async def redis(self) -> ConnectionCheck:
        return await self._redis.check()

class ProviderSettingsOperations:
    def __init__(self, commands: ProviderSettingsWriter) -> None:
        self._commands = commands

    async def update(
        self,
        provider_id: str,
        api_key: str | None,
        model: str | None,
        embedding_model: str | None,
    ) -> dict[str, object]:
        return await self._commands.update(provider_id, api_key, model, embedding_model)

    async def activate(self, provider_id: str) -> list[dict[str, object]]:
        return await self._commands.activate(provider_id)

    async def delete(self, provider_id: str) -> list[dict[str, object]]:
        return await self._commands.delete(provider_id)
