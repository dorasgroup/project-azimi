from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel


class ReportRead(BaseModel):
    id: UUID
    job_id: UUID
    title: str
    summary: str | None
    full_report: dict[str, Any]
    sources: list[dict[str, Any]]
    pdf_available: bool
    docx_available: bool
    created_at: datetime
