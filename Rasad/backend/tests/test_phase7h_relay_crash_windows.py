"""Durable relay crash windows and concurrent claims on an isolated test DB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.integration_outbox import IntegrationOutbox
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.integration.outbox import enqueue
from app.shared.integration.relay import OutboxRelay


# pytest gives each async test its own loop. NullPool prevents cross-loop reuse.
_engine = create_async_engine(settings.database_url, poolclass=NullPool)
SessionFactory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


class RecordingPublisher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def send_task(self, name: str, **kwargs: object) -> None:
        self.calls.append({"name": name, **kwargs})


async def _seed(key: str, available_at: datetime | None = None) -> IntegrationOutbox:
    async with SessionFactory() as session:
        await enqueue(
            session, message_key=key, message_type="SOURCE_EXECUTION",
            payload={"schema_version": 1, "source_task_id": str(uuid4())},
            available_at=available_at or datetime(2000, 1, 1, tzinfo=UTC),
        )
        await session.commit()
        row = await session.scalar(select(IntegrationOutbox).where(IntegrationOutbox.message_key == key))
        assert row is not None
        return row


async def _expire_claim(identifier: object) -> None:
    async with SessionFactory() as session:
        row = await session.get(IntegrationOutbox, identifier)
        assert row is not None and row.status == "PUBLISHING"
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


@pytest.mark.asyncio
async def test_crash_before_publish_is_reclaimed_by_another_relay() -> None:
    row = await _seed(f"phase7h-before:{uuid4()}")
    publisher = RecordingPublisher()
    first = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1)
    claimed = await first.claim_due()
    assert len(claimed) == 1 and claimed[0][0].id == row.id
    assert publisher.calls == []

    await _expire_claim(row.id)
    second = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1)
    assert await second.drain_once() == 1
    async with SessionFactory() as session:
        recovered = await session.get(IntegrationOutbox, row.id)
        assert recovered is not None
        assert recovered.status == "PUBLISHED" and recovered.attempt_count == 2
        assert recovered.consumer_acked_at is None
    assert len(publisher.calls) == 1
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row.id)
        await session.commit()


@pytest.mark.asyncio
async def test_crash_after_publish_before_marker_duplicates_same_logical_delivery() -> None:
    row = await _seed(f"phase7h-after:{uuid4()}")
    publisher = RecordingPublisher()
    first = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1)
    claimed = await first.claim_due()
    assert len(claimed) == 1
    await first._publish(claimed[0][0])
    assert len(publisher.calls) == 1

    await _expire_claim(row.id)
    second = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1)
    assert await second.drain_once() == 1
    async with SessionFactory() as session:
        recovered = await session.get(IntegrationOutbox, row.id)
        assert recovered is not None and recovered.status == "PUBLISHED"
        assert recovered.attempt_count == 2
    assert len(publisher.calls) == 2
    assert {call["headers"]["outbox_id"] for call in publisher.calls} == {str(row.id)}
    assert len({call["task_id"] for call in publisher.calls}) == 2
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row.id)
        await session.commit()


@pytest.mark.asyncio
async def test_published_acked_row_is_never_republished_by_safety_sweep() -> None:
    row = await _seed(f"phase7h-acked:{uuid4()}")
    publisher = RecordingPublisher()
    relay = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1,
                        unacked_retry_seconds=0)
    assert await relay.drain_once() == 1
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row.id)
        await session.commit()
    await relay.drain_once()
    assert sum(call["headers"]["outbox_id"] == str(row.id)
               for call in publisher.calls) == 1


@pytest.mark.asyncio
async def test_two_relays_claim_each_due_row_once_in_normal_cycle() -> None:
    marker = f"phase7h-multi:{uuid4()}"
    async with SessionFactory() as session:
        earliest = await session.scalar(select(func.min(IntegrationOutbox.available_at)))
    priority_due = (earliest - timedelta(days=1)) if earliest else datetime(2000, 1, 1, tzinfo=UTC)
    identifiers = {(await _seed(f"{marker}:{index}", priority_due)).id for index in range(20)}
    publisher = RecordingPublisher()
    first = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=10)
    second = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=10)
    claimed_a, claimed_b = await asyncio.gather(first.claim_due(), second.claim_due())
    claimed_ids = [row.id for row, _ in claimed_a + claimed_b]
    assert len(claimed_ids) == 20 and set(claimed_ids) == identifiers
    assert len(claimed_ids) == len(set(claimed_ids))
    for row, token in claimed_a + claimed_b:
        await first._publish(row)
        await first._finish(row.id, token)
    assert len(publisher.calls) == 20
    async with SessionFactory() as session:
        for identifier in identifiers:
            await acknowledge_in_session(session, identifier)
        await session.commit()


@pytest.mark.asyncio
async def test_same_message_key_concurrent_enqueue_creates_one_row() -> None:
    key = f"phase7h-race:{uuid4()}"
    payload = {"schema_version": 1, "source_task_id": str(uuid4())}

    async def insert_one() -> bool:
        async with SessionFactory() as session:
            inserted = await enqueue(session, message_key=key,
                                     message_type="SOURCE_EXECUTION", payload=payload,
                                     available_at=datetime(2000, 1, 1, tzinfo=UTC))
            await session.commit()
            return inserted

    outcomes = await asyncio.gather(*(insert_one() for _ in range(10)))
    assert outcomes.count(True) == 1
    async with SessionFactory() as session:
        rows = (await session.scalars(select(IntegrationOutbox).where(
            IntegrationOutbox.message_key == key))).all()
        assert len(rows) == 1
        row_id = rows[0].id
    relay = OutboxRelay(sessions=SessionFactory, publisher=RecordingPublisher(), batch_size=1)
    assert await relay.drain_once() == 1
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row_id)
        await session.commit()


@pytest.mark.asyncio
async def test_missed_notify_is_reconciled_when_relay_starts_later() -> None:
    # _seed commits and emits NOTIFY while no listener/relay is running.
    row = await _seed(f"phase7h-missed-notify:{uuid4()}")
    publisher = RecordingPublisher()
    relay = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1)
    assert await relay.drain_once() == 1
    assert publisher.calls[0]["headers"]["outbox_id"] == str(row.id)
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row.id)
        await session.commit()


@pytest.mark.asyncio
async def test_published_unacked_row_republishes_after_broker_loss() -> None:
    row = await _seed(f"phase7h-unacked:{uuid4()}")
    publisher = RecordingPublisher()
    relay = OutboxRelay(sessions=SessionFactory, publisher=publisher, batch_size=1,
                        unacked_retry_seconds=0)
    assert await relay.drain_once() == 1
    async with SessionFactory() as session:
        persisted = await session.get(IntegrationOutbox, row.id)
        assert persisted is not None and persisted.status == "PUBLISHED"
        assert persisted.consumer_acked_at is None
    assert await relay.drain_once() == 1
    assert len(publisher.calls) == 2
    assert publisher.calls[0]["headers"] == publisher.calls[1]["headers"]
    async with SessionFactory() as session:
        await acknowledge_in_session(session, row.id)
        await session.commit()
    await relay.drain_once()
    assert sum(call["headers"]["outbox_id"] == str(row.id)
               for call in publisher.calls) == 2
