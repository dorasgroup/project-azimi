"""Run-level analysis specification entity, digest computation, and secret validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping
from uuid import UUID

from app.modules.analysis.domain.source_analysis import FORBIDDEN_SPEC_SECRET_PATTERNS


def compute_run_spec_digest(spec: Mapping[str, object]) -> str:
    """Compute deterministic SHA-256 digest of a run analysis spec."""
    raw = json.dumps(dict(spec), sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_run_spec_secrets(spec: Mapping[str, object]) -> None:
    """Validate that spec dictionary contains zero secrets or credentials."""
    for key, val in spec.items():
        key_lower = str(key).lower()
        for pattern in FORBIDDEN_SPEC_SECRET_PATTERNS:
            if pattern in key_lower:
                raise ValueError(f"Secret-like key '{key}' is forbidden in RunAnalysisSpec")
        if isinstance(val, str):
            val_lower = val.lower()
            if val_lower.startswith(("bearer ", "sk-", "teb1.")):
                raise ValueError(f"Secret-like token in field '{key}' is forbidden in RunAnalysisSpec")
        elif isinstance(val, Mapping):
            validate_run_spec_secrets(val)


@dataclass(frozen=True)
class RunAnalysisSpec:
    """Immutable snapshot of the run-level analysis configuration.

    Owned strictly by the Analysis bounded context. Guarantees that every
    SourceAnalysis within a single MonitoringRun shares the identical configuration snapshot.
    """
    id: UUID
    run_id: UUID
    spec: dict[str, object]
    spec_digest: str
    created_at: datetime

    def __post_init__(self) -> None:
        validate_run_spec_secrets(self.spec)
