from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, Field


class TrendContentItem(BaseModel):
    external_id: str = Field(min_length=1, max_length=200)
    source_name: str = Field(min_length=1, max_length=255)
    source_url: AnyHttpUrl | None = None
    published_at: datetime | None = None
    text: str = Field(min_length=1, max_length=20_000)


class TrendImportRequest(BaseModel):
    source_system: str = Field(
        default="trend-monitor",
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    source_trend_id: str = Field(min_length=1, max_length=200)
    source_version: int = Field(default=1, ge=1)
    title: str = Field(min_length=1, max_length=500)
    topic: str | None = Field(default=None, max_length=255)
    score: float | None = None
    detected_at: datetime
    status: Literal["trending", "confirmed"]
    summary: str = Field(min_length=1, max_length=100_000)
    items: list[TrendContentItem] = Field(default_factory=list, max_length=100)


class TrendImportResponse(BaseModel):
    id: UUID
    source_system: str
    source_trend_id: str
    source_version: int
    content_sha256: str
    title: str
    topic: str | None
    category_names: list[str] = Field(default_factory=list)
    trend_status: str
    score: float | None
    detected_at: datetime
    upload_id: UUID | None
    ingestion_job_id: UUID | None
    delivery_status: str
    dify_status: str
    dify_document_id: UUID | None
    received_at: datetime
    dify_completed_at: datetime | None
    duplicate: bool = False


class TrendImportListResponse(BaseModel):
    items: list[TrendImportResponse]
    total: int
