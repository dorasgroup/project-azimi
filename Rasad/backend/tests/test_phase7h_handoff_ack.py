"""Atomic consumer acceptance of short outbox handoffs."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.integration_outbox import IntegrationOutbox
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.integration.outbox import enqueue


class TestShortHandoffAck(IsolatedAsyncioTestCase):
    async def test_ack_and_successor_roll_back_together_then_commit_together(self) -> None:
        message_key = f"phase7h-parent:{uuid4()}"
        successor_key = f"phase7h-successor:{uuid4()}"
        async with SessionFactory() as session:
            await enqueue(
                session, message_key=message_key,
                message_type="SOURCE_ANALYSIS_HANDOFF",
                payload={"schema_version": 1, "source_task_id": str(uuid4())},
            )
            await session.commit()
            parent_id = (await session.scalar(
                select(IntegrationOutbox.id).where(IntegrationOutbox.message_key == message_key)
            ))
            self.assertIsNotNone(parent_id)

        try:
            async with SessionFactory() as session:
                await enqueue(
                    session, message_key=successor_key,
                    message_type="SOURCE_ANALYSIS",
                    payload={"schema_version": 1, "source_analysis_id": str(uuid4())},
                )
                await acknowledge_in_session(session, parent_id)
                raise RuntimeError("injected crash before commit")
        except RuntimeError:
            pass

        async with SessionFactory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            successor = await session.scalar(
                select(IntegrationOutbox).where(IntegrationOutbox.message_key == successor_key)
            )
            self.assertIsNone(parent.consumer_acked_at)
            self.assertIsNone(successor)

            await enqueue(
                session, message_key=successor_key,
                message_type="SOURCE_ANALYSIS",
                payload={"schema_version": 1, "source_analysis_id": str(uuid4())},
            )
            await acknowledge_in_session(session, parent_id)
            await session.commit()

        async with SessionFactory() as session:
            parent = await session.get(IntegrationOutbox, parent_id)
            successor = await session.scalar(
                select(IntegrationOutbox).where(IntegrationOutbox.message_key == successor_key)
            )
            self.assertIsNotNone(parent.consumer_acked_at)
            self.assertIsNotNone(successor)
