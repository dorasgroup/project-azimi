"""Record durable consumer acceptance of an outbox delivery."""

from __future__ import annotations

import asyncio
from uuid import UUID

import asyncpg
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.integration_outbox import IntegrationOutbox


def outbox_id_from_request(request: object) -> UUID | None:
    """Return the durable message ID, if this is a relay-originated delivery."""
    headers = getattr(request, "headers", None) or {}
    message_id = headers.get("outbox_id")
    return UUID(str(message_id)) if message_id is not None else None


async def acknowledge_in_session(session: AsyncSession, outbox_id: UUID | None) -> None:
    """ACK inside the transaction that creates the durable successor obligation."""
    if outbox_id is None:
        return
    result = await session.execute(
        update(IntegrationOutbox)
        .where(IntegrationOutbox.id == outbox_id, IntegrationOutbox.status != "FAILED")
        .values(
            consumer_acked_at=func.coalesce(IntegrationOutbox.consumer_acked_at, func.now()),
            updated_at=func.now(),
        )
    )
    if result.rowcount != 1:
        raise ValueError(f"Outbox message is not ACK-eligible: {outbox_id}")


def acknowledge_outbox(request: object) -> None:
    identifier = outbox_id_from_request(request)
    if identifier is None:
        return  # Legacy/test messages were not emitted by the relay.

    async def _ack() -> None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        connection = await asyncpg.connect(dsn, timeout=5)
        try:
            await connection.execute(
                "UPDATE integration_outbox SET consumer_acked_at = COALESCE(consumer_acked_at, now()), "
                "updated_at = now() WHERE id = $1 AND status <> 'FAILED'",
                identifier,
            )
        finally:
            await connection.close()

    asyncio.run(_ack())
