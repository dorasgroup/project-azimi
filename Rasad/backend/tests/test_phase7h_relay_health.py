"""Outbox relay health must reflect both durable DB and broker availability."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.shared.integration import relay


_engine = create_async_engine(settings.database_url, poolclass=NullPool)
TestSessions = async_sessionmaker(_engine, expire_on_commit=False,
                                  class_=AsyncSession)


@pytest.fixture(autouse=True)
def isolated_db_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(relay, "SessionFactory", TestSessions)


@pytest.mark.asyncio
async def test_relay_health_checks_broker_after_database(monkeypatch: pytest.MonkeyPatch) -> None:
    broker = AsyncMock()
    broker.ping.return_value = True
    monkeypatch.setattr(relay.redis_asyncio, "from_url", lambda *_args, **_kwargs: broker)

    await relay.healthcheck()

    broker.ping.assert_awaited_once()
    broker.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_relay_health_is_degraded_when_broker_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = AsyncMock()
    broker.ping.side_effect = RedisConnectionError("isolated broker unavailable")
    monkeypatch.setattr(relay.redis_asyncio, "from_url", lambda *_args, **_kwargs: broker)

    with pytest.raises(RedisConnectionError):
        await relay.healthcheck()

    broker.aclose.assert_awaited_once()
