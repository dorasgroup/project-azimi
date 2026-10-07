"""Independent PostgreSQL-to-Celery outbox relay; no business decisions live here."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID, uuid4

import asyncpg
from redis import asyncio as redis_asyncio
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import SessionFactory
from app.models.integration_outbox import IntegrationOutbox
from app.shared.integration.outbox import OUTBOX_CHANNEL

logger = logging.getLogger("rasad.integration.outbox")

ROUTES: dict[str, tuple[str, str, str]] = {
    "SOURCE_EXECUTION": ("monitoring.process_source_task", "monitoring.source", "source_task_id"),
    "SOURCE_ANALYSIS_HANDOFF": ("analysis.ensure_source_analysis", "analysis.source", "source_task_id"),
    "SOURCE_ANALYSIS": ("analysis.process_source_analysis", "analysis.source", "source_analysis_id"),
    "RUN_AGGREGATION_HANDOFF": ("analysis.ensure_run_aggregation", "analysis.aggregate", "run_id"),
    "RUN_AGGREGATION": ("analysis.aggregate_run", "analysis.aggregate", "aggregation_id"),
    "AGGREGATION_TERMINAL": ("monitoring.finalize_aggregation", "monitoring.control", "aggregation_id"),
    "SOURCE_TERMINAL": ("monitoring.process_control_event", "monitoring.control", ""),
    "ANALYSIS_TERMINAL": ("monitoring.process_control_event", "monitoring.control", ""),
    "ANALYSIS_READY": ("monitoring.process_control_event", "monitoring.control", ""),
    "REPORT_GENERATION_HANDOFF": ("report.ensure_generation", "report.generate", ""),
    "REPORT_GENERATION": ("report.generate_report", "report.generate", "report_generation_id"),
    "REPORT_TERMINAL": ("monitoring.finalize_report", "monitoring.control", "report_generation_id"),
    "DEADLINE_REACHED": ("monitoring.process_control_event", "monitoring.control", ""),
}


def delivery_route(row: IntegrationOutbox) -> tuple[str, str, list[object]]:
    if row.message_type not in ROUTES or row.payload_json.get("schema_version") != 1:
        raise ValueError("Invalid outbox message contract")
    task, queue, identifier = ROUTES[row.message_type]
    if identifier:
        value = row.payload_json.get(identifier)
        if not isinstance(value, str):
            raise ValueError("Missing outbox identifier")
        UUID(value)
        return task, queue, [value]
    return task, queue, [row.payload_json]


def retry_delay(attempt_count: int) -> int:
    return min(60, 2 ** min(max(attempt_count, 1), 6))


class OutboxRelay:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession] = SessionFactory,
        *,
        batch_size: int = 32,
        lease_seconds: int = 60,
        safety_sweep_seconds: int = 45,
        unacked_retry_seconds: int = 60,
        publisher: object | None = None,
    ) -> None:
        self.sessions = sessions
        self.batch_size = batch_size
        self.lease_seconds = lease_seconds
        self.safety_sweep_seconds = safety_sweep_seconds
        self.unacked_retry_seconds = unacked_retry_seconds
        self.publisher = publisher
        self.wake = asyncio.Event()

    async def claim_due(self) -> list[tuple[IntegrationOutbox, UUID]]:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            rows = (await session.scalars(
                select(IntegrationOutbox)
                .where(
                    or_(
                        and_(
                            IntegrationOutbox.status == "PENDING",
                            IntegrationOutbox.available_at <= now,
                            or_(IntegrationOutbox.next_attempt_at.is_(None), IntegrationOutbox.next_attempt_at <= now),
                        ),
                        and_(
                            IntegrationOutbox.status == "PUBLISHING",
                            IntegrationOutbox.lease_expires_at <= now,
                        ),
                        and_(
                            IntegrationOutbox.status == "PUBLISHED",
                            IntegrationOutbox.consumer_acked_at.is_(None),
                            IntegrationOutbox.published_at <= now - timedelta(seconds=self.unacked_retry_seconds),
                        ),
                    )
                )
                .order_by(IntegrationOutbox.available_at, IntegrationOutbox.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )).all()
            claimed: list[tuple[IntegrationOutbox, UUID]] = []
            for row in rows:
                token = uuid4()
                row.status = "PUBLISHING"
                row.claim_token = token
                row.claimed_at = now
                row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                row.attempt_count += 1
                row.updated_at = now
                claimed.append((row, token))
            await session.commit()
            return claimed

    async def _finish(self, message_id: UUID, token: UUID, error: Exception | None = None) -> None:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            row = await session.scalar(
                select(IntegrationOutbox)
                .where(IntegrationOutbox.id == message_id, IntegrationOutbox.claim_token == token,
                       IntegrationOutbox.status == "PUBLISHING")
                .with_for_update()
            )
            if row is None:
                return
            if error is None:
                row.status = "PUBLISHED"
                row.published_at = now
                # Consumer ACK may have arrived before the publish marker.
                # Preserve it; otherwise a harmless duplicate will be retried.
                row.last_error_code = None
            else:
                row.status = "FAILED" if isinstance(error, ValueError) else "PENDING"
                row.next_attempt_at = None if row.status == "FAILED" else now + timedelta(seconds=retry_delay(row.attempt_count))
                row.last_error_code = type(error).__name__[:100]
                row.last_error_at = now
            row.claim_token = None
            row.claimed_at = None
            row.lease_expires_at = None
            row.updated_at = now
            await session.commit()

    async def _publish(self, row: IntegrationOutbox) -> None:
        task, queue, args = delivery_route(row)
        if self.publisher is None:
            from app.workers.celery_app import celery_app
            publisher = celery_app
        else:
            publisher = self.publisher
        # Fail fast on broker outage. The durable DB row, not Kombu retry, owns recovery.
        await asyncio.to_thread(
            publisher.send_task, task, args=args, queue=queue,
            task_id=str(uuid4()), headers={"outbox_id": str(row.id)}, retry=False,
        )

    async def drain_once(self) -> int:
        claimed = await self.claim_due()
        for row, token in claimed:
            try:
                await self._publish(row)
            except Exception as exc:
                logger.warning("Outbox delivery failed for %s (%s)", row.id, type(exc).__name__)
                await self._finish(row.id, token, exc)
            else:
                await self._finish(row.id, token)
        return len(claimed)

    async def next_wake_seconds(self) -> float:
        now = datetime.now(UTC)
        async with self.sessions() as session:
            pending = await session.scalar(select(func.min(func.greatest(
                IntegrationOutbox.available_at,
                func.coalesce(IntegrationOutbox.next_attempt_at, IntegrationOutbox.available_at),
            ))).where(IntegrationOutbox.status == "PENDING"))
            leased = await session.scalar(select(func.min(IntegrationOutbox.lease_expires_at))
                                          .where(IntegrationOutbox.status == "PUBLISHING"))
            unacked = await session.scalar(select(func.min(IntegrationOutbox.published_at))
                                           .where(IntegrationOutbox.status == "PUBLISHED",
                                                  IntegrationOutbox.consumer_acked_at.is_(None)))
        if unacked is not None:
            unacked += timedelta(seconds=self.unacked_retry_seconds)
        due = min((value for value in (pending, leased, unacked) if value is not None), default=None)
        if due is None:
            return float(self.safety_sweep_seconds)
        return max(0.1, min(float(self.safety_sweep_seconds), (due - now).total_seconds()))

    async def listen(self) -> None:
        dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        while True:
            connection = None
            try:
                connection = await asyncpg.connect(dsn)
                await connection.add_listener(OUTBOX_CHANNEL, lambda *_: self.wake.set())
                self.wake.set()  # Reconcile notifications missed during disconnect.
                while True:
                    await asyncio.sleep(15)
                    await connection.execute("SELECT 1")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Outbox LISTEN disconnected (%s); reconnecting", type(exc).__name__)
                self.wake.set()
                await asyncio.sleep(5)
            finally:
                if connection is not None:
                    await connection.close()

    async def run(self) -> None:
        listener = asyncio.create_task(self.listen())
        try:
            while True:
                try:
                    while await self.drain_once() == self.batch_size:
                        pass
                    delay = await self.next_wake_seconds()
                    try:
                        await asyncio.wait_for(self.wake.wait(), timeout=delay)
                    except TimeoutError:
                        pass
                    self.wake.clear()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.exception("Outbox database error (%s); retrying", type(exc).__name__)
                    await asyncio.sleep(5)
        finally:
            listener.cancel()
            try:
                await listener
            except asyncio.CancelledError:
                pass


async def healthcheck() -> None:
    async with SessionFactory() as session:
        await session.execute(select(1))
    # A DB-only check would report healthy while every publication is failing.
    broker = redis_asyncio.from_url(
        settings.celery_broker_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        if not await broker.ping():
            raise RuntimeError("Outbox relay broker ping failed")
    finally:
        await broker.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.healthcheck:
        asyncio.run(healthcheck())
    else:
        if args.batch_size < 1 or args.batch_size > 1000:
            parser.error("batch-size must be 1..1000")
        asyncio.run(OutboxRelay(batch_size=args.batch_size).run())


if __name__ == "__main__":
    main()
