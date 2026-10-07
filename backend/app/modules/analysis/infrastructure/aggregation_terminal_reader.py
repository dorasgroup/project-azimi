"""Analysis-owned read view of an immutable terminal aggregation."""

from uuid import UUID
from app.models.run_aggregation import OrmRunAggregation
from app.shared.integration.aggregation_terminal import AggregationTerminalSnapshot


class AggregationTerminalReader:
    def __init__(self, sessions) -> None:
        self._sessions = sessions

    async def read(self, aggregation_id: UUID) -> AggregationTerminalSnapshot:
        async with self._sessions() as session:
            row = await session.get(OrmRunAggregation, aggregation_id)
            if row is None or row.status != "failed":
                raise ValueError("Aggregation event lacks durable failed outcome")
            reason = row.error_code if row.error_code in {"SOURCE_ANALYSIS_FAILED", "NO_RELEVANT_EVIDENCE", "AGGREGATION_INVALID", "AGGREGATION_RETRY_EXHAUSTED"} else None
            return AggregationTerminalSnapshot(row.id, row.run_id, row.status, reason)

