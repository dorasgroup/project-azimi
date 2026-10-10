"""Framework-independent data contracts for document preparation."""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping


@dataclass(frozen=True)
class RawDocumentPage:
    """Raw crawler output accepted by the document-preparation boundary."""

    url: str
    final_url: str | None
    success: bool
    status_code: int | None = None
    title: str | None = None
    html: str | None = None
    markdown: str | None = None
    content_hash: str | None = None
    cache_status: str | None = None
    error: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedDocumentRecord:
    """Document ready for persistence, independent from Pydantic and SQLAlchemy."""

    url: str
    canonical_url: str
    title: str
    clean_text: str
    markdown: str | None
    author: str | None
    source_name: str | None
    published_at: datetime | None
    published_at_jalali: str | None
    language: str
    lead_image_url: str | None
    matched_score: float
    matched_terms: tuple[str, ...]
    content_hash: str
    published_at_source: str | None = None
    published_at_confidence: float | None = None
    published_at_raw: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RejectedDocumentRecord:
    url: str
    reason: str


@dataclass(frozen=True)
class DocumentPreparationRequest:
    pages: tuple[RawDocumentPage, ...]
    topic: str
    source_name: str | None
    date_from: date | None
    date_to: date | None
    policy: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentPreparationOutcome:
    documents: tuple[PreparedDocumentRecord, ...]
    rejected: tuple[RejectedDocumentRecord, ...]
    duplicate_count: int
