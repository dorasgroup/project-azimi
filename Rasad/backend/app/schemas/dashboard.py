from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import JobStatus


class DashboardCounts(BaseModel):
    users: int = Field(ge=0)
    sources: int = Field(ge=0)
    active_sources: int = Field(ge=0)
    bot_identities: int = Field(ge=0)
    monitoring_jobs: int = Field(ge=0)
    reports: int = Field(ge=0)
    system_errors: int = Field(ge=0)


class DashboardJobSummary(BaseModel):
    id: UUID
    topic: str
    status: JobStatus
    created_at: datetime
    finished_at: datetime | None
    error_message: str | None


class DashboardReportSummary(BaseModel):
    id: UUID
    job_id: UUID
    title: str
    summary: str | None
    created_at: datetime
    source_count: int = Field(ge=0)


class DashboardSummary(BaseModel):
    counts: DashboardCounts
    jobs_by_status: dict[JobStatus, int]
    recent_jobs: list[DashboardJobSummary]
    recent_reports: list[DashboardReportSummary]
