"""Port definitions for run-level analysis aggregation."""

from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol, Sequence
from uuid import UUID

from app.modules.analysis.domain.run_aggregation import (
    AggregatedAnalysisResultData,
    AggregationClaimOutcome,
    RunAggregation,
)


class RunAggregationRepositoryPort(Protocol):
    """Port for persisting and managing RunAggregation lifecycle and input manifests."""

    async def ensure_aggregation_and_manifest(
        self,
        run_id: UUID,
        readiness_reason: str,
        cutoff_at: datetime | None,
        completion_metadata: Mapping[str, object],
    ) -> RunAggregation | None:
        """Idempotently ensure a RunAggregation exists and snapshot its input manifest."""
        ...

    async def get_by_id(self, aggregation_id: UUID) -> RunAggregation | None:
        """Fetch RunAggregation by ID."""
        ...

    async def get_by_run_id(self, run_id: UUID) -> RunAggregation | None:
        """Fetch RunAggregation by run ID."""
        ...

    async def get_run_coverage(
        self,
        run_id: UUID,
    ) -> Mapping[str, object]:
        """Fetch snapshot of run source counters."""
        ...

    async def claim_explicit(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> AggregationClaimOutcome:
        """Attempt to claim the aggregation execution with lease fencing."""
        ...

    async def release_claim(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
    ) -> bool:
        """Release claim for transient retry back to pending."""
        ...

    async def renew_lease(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        lease_duration_seconds: int,
        now: datetime,
    ) -> bool:
        """Renew active heartbeat lease for running aggregation."""
        ...

    async def save_result_and_complete(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        result: AggregatedAnalysisResultData,
        now: datetime,
    ) -> bool:
        """Atomically persist aggregated result and transition aggregation to completed."""
        ...

    async def mark_failed(
        self,
        aggregation_id: UUID,
        claim_token: UUID,
        error_code: str,
        error_message: str,
        now: datetime,
    ) -> bool:
        """Mark aggregation failed."""
        ...

    async def load_manifest_inputs(
        self,
        aggregation_id: UUID,
    ) -> list[dict[str, object]]:
        """Load the structured input manifest items along with source analysis summaries and findings."""
        ...


class RunAggregatorPort(Protocol):
    """Port for generating aggregated structured findings from source analysis inputs."""

    async def aggregate(
        self,
        spec: Mapping[str, object],
        inputs: Sequence[Mapping[str, object]],
        coverage_metadata: Mapping[str, object],
    ) -> dict[str, object]:
        """Aggregate inputs into structured summary, findings, and provenance."""
        ...


class MonitoringAnalysisReadyPublisherPort(Protocol):
    """Port for publishing the MonitoringAnalysisReady integration event."""

    def publish_analysis_ready(
        self,
        run_id: UUID,
        aggregation_id: UUID,
        result_id: UUID,
    ) -> None:
        """Publish analysis ready integration event."""
        ...


class SourceAnalysisTerminalPublisherPort(Protocol):
    """Port for publishing source analysis terminal integration events."""

    def publish_source_analysis_terminal(
        self,
        run_id: UUID,
        source_analysis_id: UUID,
        outcome: str,
        occurred_at: datetime,
    ) -> None:
        """Publish source analysis terminal signal."""
        ...
