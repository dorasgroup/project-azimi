"""Insert immutable integration intents in the caller's business transaction."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_outbox import IntegrationOutbox

OUTBOX_CHANNEL = "rasad_integration_outbox"

# The integration log is durable and retained. Its payload is a routing
# contract, never a place for credentials, document text or model inputs.
PAYLOAD_FIELDS: dict[str, frozenset[str]] = {
    "SOURCE_EXECUTION": frozenset({"schema_version", "source_task_id"}),
    "SOURCE_ANALYSIS_HANDOFF": frozenset({"schema_version", "source_task_id"}),
    "SOURCE_ANALYSIS": frozenset({"schema_version", "source_analysis_id"}),
    "SOURCE_TERMINAL": frozenset({
        "schema_version", "event", "run_id", "source_task_id", "outcome", "occurred_at",
    }),
    "ANALYSIS_TERMINAL": frozenset({
        "schema_version", "event", "run_id", "source_analysis_id", "outcome", "occurred_at",
    }),
    "RUN_AGGREGATION_HANDOFF": frozenset({"schema_version", "run_id"}),
    "RUN_AGGREGATION": frozenset({"schema_version", "aggregation_id"}),
    "AGGREGATION_TERMINAL": frozenset({"schema_version", "aggregation_id"}),
    "ANALYSIS_READY": frozenset({
        "schema_version", "event", "run_id", "aggregation_id", "result_id",
    }),
    "REPORT_GENERATION_HANDOFF": frozenset({
        "schema_version", "run_id", "result_id",
    }),
    "REPORT_GENERATION": frozenset({"schema_version", "report_generation_id"}),
    "REPORT_TERMINAL": frozenset({"schema_version", "report_generation_id"}),
    "DEADLINE_REACHED": frozenset({
        "schema_version", "event", "run_id", "tracker_id", "deadline_at",
    }),
}


def _primitive(value: object) -> bool:
    if value is None or isinstance(value, (str, int, bool)):
        return True
    if isinstance(value, list):
        return all(_primitive(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _primitive(item) for key, item in value.items())
    return False


async def enqueue(
    session: AsyncSession,
    *,
    message_key: str,
    message_type: str,
    payload: dict[str, object],
    available_at: datetime | None = None,
) -> bool:
    """Ensure one immutable logical message; never commit the caller's session."""
    if not message_key or len(message_key) > 200 or not message_type or len(message_type) > 64:
        raise ValueError("Invalid outbox routing metadata")
    if payload.get("schema_version") != 1 or not _primitive(payload):
        raise ValueError("Outbox payload must be versioned primitive identifiers")
    allowed_fields = PAYLOAD_FIELDS.get(message_type)
    if allowed_fields is None or not payload.keys() <= allowed_fields:
        raise ValueError("Outbox payload contains unsupported routing fields")
    if any(isinstance(value, (dict, list)) for value in payload.values()):
        raise ValueError("Outbox routing payload must be flat")
    for field, value in payload.items():
        if field.endswith("_id") and value is not None:
            try:
                UUID(str(value))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ValueError("Outbox routing identifier must be a UUID") from exc
        elif isinstance(value, str) and len(value) > 80:
            raise ValueError("Outbox routing value is too long")
    due = available_at or datetime.now(UTC)
    if due.tzinfo is None:
        raise ValueError("Outbox available_at must be timezone-aware")
    result = await session.execute(
        insert(IntegrationOutbox)
        .values(
            id=uuid4(), message_key=message_key, message_type=message_type,
            payload_json=payload, status="PENDING", available_at=due,
        )
        .on_conflict_do_nothing(index_elements=["message_key"])
    )
    inserted = (result.rowcount or 0) > 0
    if inserted:
        # PostgreSQL emits NOTIFY only after this transaction commits.
        await session.execute(select(func.pg_notify(OUTBOX_CHANNEL, "new")))
    return inserted
