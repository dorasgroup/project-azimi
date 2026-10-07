from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field


class ProcessingPolicy(BaseModel):
    min_text_length: int = Field(default=200, ge=1, le=10000)
    min_matched_score: float = Field(default=0.1, ge=0, le=1)
    title_similarity_threshold: float = Field(default=0.9, ge=0.5, le=1)
    content_similarity_threshold: float = Field(default=0.92, ge=0.5, le=1)
    min_publication_date_confidence: float = Field(default=0.55, ge=0, le=1)


class PreparedDocument(BaseModel):
    url: AnyHttpUrl
    canonical_url: AnyHttpUrl
    title: str
    clean_text: str
    markdown: str | None = None
    author: str | None = None
    source_name: str | None = None
    published_at: datetime | None = None
    published_at_jalali: str | None = None
    published_at_source: str | None = None
    published_at_confidence: float | None = Field(default=None, ge=0, le=1)
    published_at_raw: str | None = None
    language: str
    lead_image_url: AnyHttpUrl | None = None
    matched_score: float = Field(ge=0, le=1)
    matched_terms: list[str] = Field(default_factory=list)
    content_hash: str
    metadata: dict[str, str] = Field(default_factory=dict)


class RejectedDocument(BaseModel):
    url: AnyHttpUrl
    reason: str


class ProcessingBatchResult(BaseModel):
    documents: list[PreparedDocument]
    rejected: list[RejectedDocument]
    duplicate_count: int = 0
