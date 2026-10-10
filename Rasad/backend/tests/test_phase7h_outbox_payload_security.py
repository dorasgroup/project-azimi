"""Outbox payloads are durable routing identifiers, not data or secrets."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.models.integration_outbox import IntegrationOutbox
from app.shared.integration.outbox import PAYLOAD_FIELDS, enqueue


class TestOutboxPayloadSecurity(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(settings.database_url, poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_every_routed_type_has_a_field_allowlist(self) -> None:
        from app.shared.integration.relay import ROUTES

        self.assertEqual(set(PAYLOAD_FIELDS), set(ROUTES))
        for fields in PAYLOAD_FIELDS.values():
            self.assertIn("schema_version", fields)
            self.assertFalse(fields & {
                "password", "api_key", "bearer_token", "database_url",
                "credentialed_dsn", "document_content", "clean_text", "prompt",
            })

    async def test_sensitive_and_nested_payloads_fail_before_insert(self) -> None:
        forbidden = [
            {"password": "secret"},
            {"api_key": "secret"},
            {"database_url": "postgresql://user:secret@host/db"},
            {"document_content": "full text"},
            {"source_task_id": {"nested": "not-an-id"}},
            {"source_task_id": "not-a-uuid"},
            {"source_task_id": "x" * 200},
        ]
        async with self.sessions() as session:
            for extra in forbidden:
                key = f"phase7h-rejected:{uuid4()}"
                with self.assertRaises(ValueError):
                    await enqueue(
                        session, message_key=key, message_type="SOURCE_EXECUTION",
                        payload={"schema_version": 1, "source_task_id": str(uuid4()), **extra},
                    )
                self.assertIsNone(await session.scalar(select(IntegrationOutbox).where(
                    IntegrationOutbox.message_key == key)))

    async def test_persisted_representative_row_contains_only_allowed_identifiers(self) -> None:
        key = f"phase7h-security:{uuid4()}"
        task_id = uuid4()
        async with self.sessions() as session:
            inserted = await enqueue(
                session, message_key=key, message_type="SOURCE_EXECUTION",
                payload={"schema_version": 1, "source_task_id": str(task_id)},
            )
            self.assertTrue(inserted)
            await session.commit()
            row = await session.scalar(select(IntegrationOutbox).where(
                IntegrationOutbox.message_key == key))
            self.assertIsNotNone(row)
            self.assertEqual(row.payload_json, {
                "schema_version": 1, "source_task_id": str(task_id),
            })

    async def test_all_current_persisted_rows_match_the_routing_allowlist(self) -> None:
        async with self.sessions() as session:
            rows = (await session.scalars(select(IntegrationOutbox))).all()
            for row in rows:
                allowed = PAYLOAD_FIELDS.get(row.message_type)
                self.assertIsNotNone(allowed, str(row.id))
                self.assertEqual(row.payload_json.get("schema_version"), 1, str(row.id))
                self.assertLessEqual(row.payload_json.keys(), allowed, str(row.id))
                self.assertFalse(any(isinstance(value, (dict, list))
                                     for value in row.payload_json.values()), str(row.id))
