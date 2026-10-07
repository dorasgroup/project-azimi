"""Monitoring owns failed run/job finalization after Analysis terminal commit."""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID
from app.shared.integration.aggregation_terminal import AggregationTerminalSnapshot


class AggregationTerminalView(Protocol):
    async def read(self, aggregation_id: UUID) -> AggregationTerminalSnapshot: ...


class MonitoringAggregationFinalization(Protocol):
    async def finalize(self, outcome: AggregationTerminalSnapshot, outbox_id: UUID | None, now: datetime) -> str: ...


class FinalizeMonitoringAggregation:
    def __init__(self, reader: AggregationTerminalView, repository: MonitoringAggregationFinalization) -> None:
        self._reader, self._repository = reader, repository

    async def execute(self, aggregation_id: UUID, outbox_id: UUID | None) -> dict[str, object]:
        outcome = await self._reader.read(aggregation_id)
        if outcome.status != "failed":
            raise ValueError("Invalid aggregation terminal outcome")
        status = await self._repository.finalize(outcome, outbox_id, datetime.now(UTC))
        return {"status": status, "run_id": str(outcome.run_id)}

