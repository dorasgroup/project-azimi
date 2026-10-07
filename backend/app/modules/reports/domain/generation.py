"""Immutable, secret-free contract for a requested report generation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping
from uuid import UUID


class ReportGenerationStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReportClaimKind(StrEnum):
    CLAIMED = "claimed"
    ACTIVE_LEASE = "active_lease"
    ALREADY_TERMINAL = "already_terminal"
    RUN_CANCELLED = "run_cancelled"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ReportClaim:
    kind: ReportClaimKind
    token: UUID | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0


REPORT_SPEC_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.0"
REPORT_TEMPLATE_VERSION = "1.0"
REQUIRED_FORMATS = ("pdf", "docx")


def report_spec_digest(data: Mapping[str, object]) -> str:
    """Fingerprint the allowlisted snapshot, even if validation rejects its values."""
    canonical = json.dumps(
        dict(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True)
class ReportGenerationSpec:
    """Snapshot only the options already used by the current report product."""

    title: str
    created_by: UUID
    date_from_jalali: str
    date_to_jalali: str
    locale: str = "fa-IR"
    formats: tuple[str, ...] = REQUIRED_FORMATS
    schema_version: str = REPORT_SCHEMA_VERSION
    template_version: str = REPORT_TEMPLATE_VERSION
    spec_version: str = REPORT_SPEC_VERSION

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("Report title is invalid")
        if self.formats != REQUIRED_FORMATS:
            raise ValueError("Only the existing PDF and DOCX artifact set is supported")
        if self.locale != "fa-IR":
            raise ValueError("Unsupported report locale")
        if (
            self.spec_version != REPORT_SPEC_VERSION
            or self.schema_version != REPORT_SCHEMA_VERSION
            or self.template_version != REPORT_TEMPLATE_VERSION
        ):
            raise ValueError("Unsupported report spec or template version")
        if not self.date_from_jalali or not self.date_to_jalali:
            raise ValueError("Report date range is required")

    def to_json(self) -> dict[str, object]:
        return {
            "spec_version": self.spec_version,
            "schema_version": self.schema_version,
            "template_version": self.template_version,
            "title": self.title,
            "created_by": str(self.created_by),
            "date_from_jalali": self.date_from_jalali,
            "date_to_jalali": self.date_to_jalali,
            "locale": self.locale,
            "formats": list(self.formats),
        }

    @property
    def digest(self) -> str:
        return report_spec_digest(self.to_json())

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> ReportGenerationSpec:
        required = {
            "spec_version", "schema_version", "template_version", "title",
            "created_by", "date_from_jalali", "date_to_jalali", "locale", "formats",
        }
        if data.keys() != required or not isinstance(data["formats"], list):
            raise ValueError("Invalid immutable report spec")
        if not all(isinstance(data[key], str) for key in required - {"formats"}):
            raise ValueError("Report spec scalar fields must be strings")
        if not all(isinstance(item, str) for item in data["formats"]):
            raise ValueError("Invalid report formats")
        return cls(
            title=str(data["title"]),
            created_by=UUID(str(data["created_by"])),
            date_from_jalali=str(data["date_from_jalali"]),
            date_to_jalali=str(data["date_to_jalali"]),
            locale=str(data["locale"]),
            formats=tuple(data["formats"]),
            schema_version=str(data["schema_version"]),
            template_version=str(data["template_version"]),
            spec_version=str(data["spec_version"]),
        )


@dataclass(frozen=True)
class ReportGenerationInput:
    """Only immutable Phase 7 result fields, plus the frozen report spec."""

    generation_id: UUID
    run_id: UUID
    aggregated_result_id: UUID
    manifest_digest: str
    aggregate_schema_version: str
    manifest_schema_version: str
    readiness_reason: str
    cutoff_at: datetime | None
    expected_sources: int
    successful_sources: int
    failed_sources: int
    cancelled_sources: int
    analysis_successful: int
    analysis_failed: int
    analysis_cancelled: int
    summary: str
    findings: tuple[Mapping[str, object], ...]
    coverage_metadata: Mapping[str, object]
    provenance_references: tuple[Mapping[str, object], ...]
    spec: ReportGenerationSpec
    report_snapshot: Mapping[str, object] | None = None
