"""Domain entities, enums, specs, and invariants for per-source analysis."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Mapping, Sequence
from uuid import UUID

SOURCE_ANALYSIS_LEASE_SECONDS: int = 300
SOURCE_ANALYSIS_HEARTBEAT_SECONDS: int = 30
SOURCE_ANALYSIS_MAX_ATTEMPTS: int = 3

FORBIDDEN_SPEC_SECRET_PATTERNS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "credential",
    "auth_token",
    "bearer_token",
    "access_token",
    "private_key",
)


class AnalysisError(Exception):
    """Base exception for single-source cognitive analysis errors."""
    pass


class TransientAnalysisError(AnalysisError):
    """Retryable failure (network timeout, rate limit 429, temporary 502/503/504)."""
    pass


class PermanentAnalysisError(AnalysisError):
    """Non-retryable failure (invalid schema, malformed prompt, unsupported configuration)."""
    pass


class NoRelevantEvidenceError(PermanentAnalysisError):
    """No accepted evidence exists; never synthesize or retry an empty report."""


class SourceAnalysisUnavailableError(PermanentAnalysisError):
    """No usable evidence survived failed analysis; not a semantic no-match."""


def classify_analysis_error(exc: Exception) -> AnalysisError:
    """Classify an arbitrary exception into TransientAnalysisError or PermanentAnalysisError."""
    if isinstance(exc, (TransientAnalysisError, PermanentAnalysisError)):
        return exc

    err_str = str(exc)
    err_lower = err_str.casefold()

    # Network / timeout / transport errors are transient
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return TransientAnalysisError(f"Operation timed out: {err_str}")

    exc_type_name = type(exc).__name__
    exc_module = getattr(type(exc), "__module__", "")
    if "httpx" in exc_module or exc_type_name in (
        "TimeoutException", "NetworkError", "TransportError",
        "ConnectTimeout", "ReadTimeout", "WriteTimeout", "PoolTimeout",
    ):
        return TransientAnalysisError(f"HTTP transport/network error: {err_str}")

    # Check for HTTP status codes and transient indicators
    if "429" in err_str or "rate limit" in err_lower:
        return TransientAnalysisError(f"Rate limited by provider: {err_str}")

    for code in ("502", "503", "504", "500", "bad gateway", "service unavailable", "gateway timeout"):
        if code in err_lower:
            return TransientAnalysisError(f"Temporary provider outage ({code}): {err_str}")

    if "unavailable within the deadline" in err_lower or "timed out" in err_lower:
        return TransientAnalysisError(f"Provider deadline exceeded: {err_str}")

    # Permanent failures (HTTP 400, 401, 403, 404, 422, validation, format)
    return PermanentAnalysisError(f"Permanent analysis failure: {err_str}")


class SourceAnalysisStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisClaimStatus(str, Enum):
    CLAIMED = "claimed"
    ALREADY_TERMINAL = "already_terminal"
    ACTIVE_LEASE = "active_lease"
    RUN_CANCELLED = "run_cancelled"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class AnalysisClaimOutcome:
    status: AnalysisClaimStatus
    claim_token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0
    current_status: SourceAnalysisStatus | None = None


@dataclass(frozen=True)
class SourceAnalysisSpec:
    """Immutable snapshot of configuration required to analyze a single source.

    Contains strictly non-secret identifiers and hyperparameters. Zero credentials.
    """
    model_provider: str = "openai"
    model_name: str = "gpt-4o-mini"
    prompt_version: str = "v1"
    temperature: float = 0.1
    max_output_tokens: int = 4096
    language_policy: str = "fa"
    topic: str = ""
    date_range: str = ""
    source_title: str = ""
    source_url: str = ""
    bot_identity: str = ""
    analysis_requirements: str = ""
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        # Validate that no forbidden secret fields or credentials are included
        for field_name, value in self.to_dict().items():
            field_lower = field_name.lower()
            for pattern in FORBIDDEN_SPEC_SECRET_PATTERNS:
                if pattern in field_lower:
                    raise ValueError(f"Secret-like field '{field_name}' is forbidden in SourceAnalysisSpec")
            if isinstance(value, str):
                val_lower = value.lower()
                if val_lower.startswith(("bearer ", "sk-", "teb1.")):
                    raise ValueError(f"Secret-like token in '{field_name}' is forbidden in SourceAnalysisSpec")

    def to_dict(self) -> dict[str, object]:
        return {
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
            "language_policy": self.language_policy,
            "topic": self.topic,
            "date_range": self.date_range,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "bot_identity": self.bot_identity,
            "analysis_requirements": self.analysis_requirements,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, object] | None) -> SourceAnalysisSpec:
        if not data:
            return cls()
        return cls(
            model_provider=str(data.get("model_provider", "openai")),
            model_name=str(data.get("model_name", "gpt-4o-mini")),
            prompt_version=str(data.get("prompt_version", "v1")),
            temperature=float(data.get("temperature", 0.1)),
            max_output_tokens=int(data.get("max_output_tokens", 4096)),
            language_policy=str(data.get("language_policy", "fa")),
            topic=str(data.get("topic", "")),
            date_range=str(data.get("date_range", "")),
            source_title=str(data.get("source_title", "")),
            source_url=str(data.get("source_url", "")),
            bot_identity=str(data.get("bot_identity", "")),
            analysis_requirements=str(data.get("analysis_requirements", "")),
            schema_version=str(data.get("schema_version", "1.0")),
        )


@dataclass(frozen=True)
class SourceAnalysisEvidence:
    """Document reference providing evidence for an extracted insight."""
    document_url: str
    document_title: str | None
    excerpt: str
    published_at: str | None


@dataclass(frozen=True)
class SourceAnalysisResultData:
    """Structured, durable result of analyzing a single source's snapshot documents."""
    input_digest: str
    summary: str
    findings: list[dict[str, object]]
    evidence_map: dict[str, object]
    schema_version: str = "1.0"
    raw_result: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "input_digest": self.input_digest,
            "summary": self.summary,
            "findings": self.findings,
            "evidence_map": self.evidence_map,
            "raw_result": self.raw_result,
        }


def compute_analysis_input_digest(
    source_id: UUID,
    topic: str,
    spec: SourceAnalysisSpec,
    documents: Sequence[object],
) -> str:
    """Compute deterministic SHA-256 digest of source analysis input for provenance."""
    hasher = hashlib.sha256()
    hasher.update(str(source_id).encode("utf-8"))
    hasher.update(topic.encode("utf-8"))
    spec_json = json.dumps(spec.to_dict(), sort_keys=True, ensure_ascii=False)
    hasher.update(spec_json.encode("utf-8"))

    # Sort documents deterministically by URL
    sorted_docs = sorted(documents, key=lambda d: getattr(d, "url", ""))
    for doc in sorted_docs:
        url = str(getattr(doc, "url", ""))
        text = str(getattr(doc, "clean_text", ""))
        content_hash = str(getattr(doc, "content_hash", "") or hashlib.sha256(text.encode("utf-8")).hexdigest())
        hasher.update(url.encode("utf-8"))
        hasher.update(content_hash.encode("utf-8"))

    return hasher.hexdigest()


class SourceAnalysis:
    """Domain aggregate root for a single source analysis execution."""

    def __init__(
        self,
        id: UUID,
        source_task_id: UUID,
        run_id: UUID,
        source_id: UUID,
        status: SourceAnalysisStatus = SourceAnalysisStatus.PENDING,
        attempt_count: int = 0,
        claimed_at: datetime | None = None,
        lease_expires_at: datetime | None = None,
        claim_token: UUID | None = None,
        input_digest: str | None = None,
        analysis_spec: SourceAnalysisSpec | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        created_at: datetime | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.source_task_id = source_task_id
        self.run_id = run_id
        self.source_id = source_id
        self.status = status
        self.attempt_count = attempt_count
        self.claimed_at = claimed_at
        self.lease_expires_at = lease_expires_at
        self.claim_token = claim_token
        self.input_digest = input_digest
        self.analysis_spec = analysis_spec or SourceAnalysisSpec()
        self.error_code = error_code
        self.error_message = error_message
        self.created_at = created_at or datetime.now(UTC)
        self.started_at = started_at
        self.finished_at = finished_at
        self.updated_at = updated_at or datetime.now(UTC)

    def is_terminal(self) -> bool:
        return self.status in (
            SourceAnalysisStatus.COMPLETED,
            SourceAnalysisStatus.FAILED,
            SourceAnalysisStatus.CANCELLED,
        )

    def cancel(self, at_time: datetime | None = None) -> bool:
        """Close active ownership, retaining spec/digest/attempts/error history."""
        if self.is_terminal():
            return False
        now = at_time or datetime.now(UTC)
        self.status = SourceAnalysisStatus.CANCELLED
        self.claim_token = None
        self.lease_expires_at = None
        self.finished_at = now
        self.updated_at = now
        return True

    def can_claim(self, at_time: datetime | None = None) -> bool:
        now = at_time or datetime.now(UTC)
        if self.is_terminal():
            return False
        if self.status == SourceAnalysisStatus.PENDING:
            return True
        if self.status == SourceAnalysisStatus.RUNNING:
            return self.lease_expires_at is not None and self.lease_expires_at < now
        return False

    def claim(self, token: UUID, at_time: datetime | None = None, lease_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS) -> None:
        now = at_time or datetime.now(UTC)
        if not self.can_claim(now):
            raise ValueError(f"Cannot claim SourceAnalysis {self.id} in status {self.status}")
        self.status = SourceAnalysisStatus.RUNNING
        self.claim_token = token
        self.claimed_at = now
        self.lease_expires_at = now + timedelta(seconds=lease_seconds)
        self.attempt_count += 1
        if self.started_at is None:
            self.started_at = now
        self.updated_at = now

    def renew_lease(self, token: UUID, at_time: datetime | None = None, extension_seconds: int = SOURCE_ANALYSIS_LEASE_SECONDS) -> bool:
        now = at_time or datetime.now(UTC)
        if self.status != SourceAnalysisStatus.RUNNING:
            return False
        if self.claim_token != token:
            return False
        if self.lease_expires_at is not None and self.lease_expires_at < now:
            return False
        self.lease_expires_at = now + timedelta(seconds=extension_seconds)
        self.updated_at = now
        return True

    def release_claim(self, token: UUID, at_time: datetime | None = None) -> None:
        """Release claim on transient failure: revert RUNNING to PENDING and clear claim token."""
        now = at_time or datetime.now(UTC)
        if self.status != SourceAnalysisStatus.RUNNING or self.claim_token != token:
            raise ValueError(f"Invalid release claim attempt for SourceAnalysis {self.id}")
        self.status = SourceAnalysisStatus.PENDING
        self.claim_token = None
        self.lease_expires_at = None
        self.updated_at = now

    def complete(self, token: UUID, at_time: datetime | None = None) -> None:
        now = at_time or datetime.now(UTC)
        if self.status != SourceAnalysisStatus.RUNNING or self.claim_token != token:
            raise ValueError(f"Invalid completion attempt for SourceAnalysis {self.id}")
        self.status = SourceAnalysisStatus.COMPLETED
        self.finished_at = now
        self.updated_at = now

    def fail(self, token: UUID, error_code: str, error_message: str, at_time: datetime | None = None) -> None:
        now = at_time or datetime.now(UTC)
        if self.status != SourceAnalysisStatus.RUNNING or self.claim_token != token:
            raise ValueError(f"Invalid failure transition for SourceAnalysis {self.id}")
        self.status = SourceAnalysisStatus.FAILED
        self.error_code = error_code
        self.error_message = error_message
        self.finished_at = now
        self.updated_at = now
