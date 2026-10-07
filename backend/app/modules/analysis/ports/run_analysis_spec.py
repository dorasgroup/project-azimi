"""Port interfaces for run-level analysis configuration snapshot."""

from __future__ import annotations

from typing import Mapping, Protocol
from uuid import UUID

from app.modules.analysis.domain.run_analysis_spec import RunAnalysisSpec


class RunAnalysisSpecRepositoryPort(Protocol):
    """Port for persisting and retrieving RunAnalysisSpec snapshots."""

    async def get_by_run_id(self, run_id: UUID) -> RunAnalysisSpec | None:
        """Fetch the RunAnalysisSpec for a monitoring run, if it exists."""
        ...

    async def ensure(
        self,
        run_id: UUID,
        spec: Mapping[str, object],
        spec_digest: str,
    ) -> RunAnalysisSpec:
        """Idempotently ensure a RunAnalysisSpec exists for a run."""
        ...


class EnsureRunAnalysisSpecPort(Protocol):
    """Application port for ensuring run-level analysis configuration is frozen."""

    async def ensure_run_analysis_spec(
        self,
        run_id: UUID,
        custom_config: Mapping[str, object] | None = None,
    ) -> RunAnalysisSpec:
        """Ensure the RunAnalysisSpec for the given run_id exists and is frozen."""
        ...
