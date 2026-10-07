"""Immutable Analysis-to-Monitoring failure contract; no model data/secrets."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AggregationTerminalSnapshot:
    aggregation_id: UUID
    run_id: UUID
    status: str
    # Only an allowlisted machine code crosses the boundary, not provider text.
    reason_code: str | None = None

