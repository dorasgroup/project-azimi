from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class UploadResponse(BaseModel):
    id: str
    filename: str
    status: str
    ingestion_job_id: str | None = None
    status_url: str | None = None
    request_id: str | None = None
    message: str


class IngestionJobResponse(BaseModel):
    job_id: UUID
    upload_id: UUID
    status: str
    attempt_count: int
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    error_code: str | None = None
    message: str


class CategoryInfo(BaseModel):
    id: UUID
    name: str


class UploadListItem(BaseModel):
    id: str
    original_filename: str
    file_size_bytes: int
    mime_type: str
    status: str
    source: str = "legacy"
    classification_method: str | None = None
    classification_score: float | None = None
    classification_needs_review: bool = False
    categories: list[CategoryInfo] = []
    created_at: datetime | None = None
