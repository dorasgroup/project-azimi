"""Immutable contract between Reporting and Monitoring finalization."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class ReportTerminalSnapshot:
    generation_id: UUID
    run_id: UUID
    aggregation_id: UUID
    status: str
    required_artifacts_durable: bool
