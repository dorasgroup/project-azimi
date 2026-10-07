"""Domain entities and logic for run-level analysis aggregation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Mapping, Sequence
from uuid import UUID

MANIFEST_SCHEMA_VERSION: str = "2.0"
AGGREGATION_SCHEMA_VERSION: str = "1.0"


class RunAggregationStatus(str, Enum):
    """Lifecycle status of a RunAggregation."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (
            RunAggregationStatus.COMPLETED,
            RunAggregationStatus.FAILED,
            RunAggregationStatus.CANCELLED,
        )


class AggregationClaimStatus(str, Enum):
    """Outcome of attempting to claim a RunAggregation."""
    CLAIMED = "claimed"
    ALREADY_TERMINAL = "already_terminal"
    ACTIVE_LEASE = "active_lease"
    RUN_CANCELLED = "run_cancelled"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class AggregationClaimOutcome:
    status: AggregationClaimStatus
    claim_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    current_status: RunAggregationStatus | None = None


def compute_manifest_digest(
    inputs: Sequence[Mapping[str, object]],
    readiness_reason: str,
    spec_digest: str,
    cutoff_at: datetime | None = None,
    completion_counts: Mapping[str, int] | None = None,
    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION,
    aggregation_schema_version: str = AGGREGATION_SCHEMA_VERSION,
) -> str:
    """Compute deterministic SHA-256 digest of aggregation manifest inputs v2.

    Binds manifest schema version, aggregation schema version, run spec digest,
    readiness reason, cutoff representation, completion counts, and sorted inputs.
    """
    hasher = hashlib.sha256()
    hasher.update(manifest_schema_version.encode("utf-8"))
    hasher.update(aggregation_schema_version.encode("utf-8"))
    hasher.update(spec_digest.encode("utf-8"))
    hasher.update(readiness_reason.encode("utf-8"))

    cutoff_canonical = cutoff_at.isoformat() if cutoff_at is not None else ""
    hasher.update(cutoff_canonical.encode("utf-8"))

    # Completion snapshot counts in fixed canonical order
    counts = completion_counts or {}
    count_keys = [
        "expected_sources",
        "successful_sources",
        "failed_sources",
        "cancelled_sources",
        "analysis_successful",
        "analysis_failed",
        "analysis_cancelled",
    ]
    counts_part = ",".join(f"{k}:{int(counts.get(k, 0))}" for k in count_keys)
    hasher.update(counts_part.encode("utf-8"))

    # Sorted inputs
    sorted_inputs = sorted(inputs, key=lambda x: str(x.get("source_analysis_result_id", "")))
    for item in sorted_inputs:
        res_id = str(item.get("source_analysis_result_id", ""))
        src_id = str(item.get("source_id", ""))
        res_dig = str(item.get("result_digest", item.get("input_digest", "")))
        s_ver = str(item.get("schema_version", "1.0"))
        i_dig = str(item.get("input_digest", ""))
        item_str = f"res:{res_id}|src:{src_id}|rdig:{res_dig}|sver:{s_ver}|idig:{i_dig}"
        hasher.update(item_str.encode("utf-8"))

    return hasher.hexdigest()


@dataclass(frozen=True)
class RunAggregationInputItem:
    """Immutable input manifest entry linking a RunAggregation to a SourceAnalysisResult."""
    id: UUID
    aggregation_id: UUID
    source_analysis_result_id: UUID
    source_id: UUID
    position: int
    input_digest: str
    schema_version: str = "1.0"


@dataclass(frozen=True)
class AggregatedAnalysisResultData:
    """Structured, durable result of aggregating source analyses for a monitoring run."""
    id: UUID
    aggregation_id: UUID
    run_id: UUID
    schema_version: str
    manifest_digest: str
    readiness_reason: str
    expected_sources: int
    successful_sources: int
    failed_sources: int
    cancelled_sources: int
    analysis_successful: int
    analysis_failed: int
    analysis_cancelled: int
    summary: str
    findings: list[dict[str, object]]
    coverage_metadata: dict[str, object]
    provenance_references: list[dict[str, object]]
    raw_result: dict[str, object] | None
    created_at: datetime
    cutoff_at: datetime | None = None
    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION


@dataclass(frozen=True)
class RunAggregation:
    """Domain model representing a single run-level analysis aggregation execution."""
    id: UUID
    run_id: UUID
    status: RunAggregationStatus
    readiness_reason: str
    cutoff_at: datetime | None
    manifest_digest: str
    run_analysis_spec_digest: str
    attempt_count: int
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    claim_token: UUID | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None

    def cancel(self, now: datetime) -> RunAggregation:
        if self.status.is_terminal:
            return self
        return replace(self, status=RunAggregationStatus.CANCELLED,
                       claim_token=None, lease_expires_at=None, finished_at=now, updated_at=now)
