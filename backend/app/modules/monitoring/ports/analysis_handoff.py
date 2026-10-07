"""Application/integration port for handoff from monitoring to source analysis."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID


class SourceAnalysisHandoffPort(Protocol):
    """Clean boundary contract for handing off a completed source crawl to per-source analysis."""

    async def ensure_and_dispatch(
        self,
        source_task_id: UUID,
        run_id: UUID | None = None,
        source_id: UUID | None = None,
        topic: str = "",
        date_range: str = "",
        source_title: str = "",
        source_url: str = "",
    ) -> None:
        """Idempotently ensure SourceAnalysis exists and dispatch it to analysis worker."""
        ...
