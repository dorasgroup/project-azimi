from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.models.enums import JobStatus
from app.processing.dates import jalali_to_gregorian


class MonitoringJobCreate(BaseModel):
    topic: str = Field(min_length=2)
    description: str | None = None
    date_from_jalali: str = Field(pattern=r"^\d{4}/\d{2}/\d{2}$")
    date_to_jalali: str = Field(pattern=r"^\d{4}/\d{2}/\d{2}$")
    date_from_gregorian: date | None = None
    date_to_gregorian: date | None = None
    source_ids: list[UUID] = Field(min_length=1)
    bot_identity_id: UUID | None = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "MonitoringJobCreate":
        self.date_from_gregorian = jalali_to_gregorian(self.date_from_jalali)
        self.date_to_gregorian = jalali_to_gregorian(self.date_to_jalali)
        if self.date_from_gregorian > self.date_to_gregorian:
            raise ValueError("date_from_jalali must not be after date_to_jalali")
        return self


class MonitoringJobUpdate(MonitoringJobCreate):
    pass


class MonitoringJobProgress(BaseModel):
    stage: str
    percent: int = Field(ge=0, le=100)
    sources_completed: int = Field(ge=0)
    sources_total: int = Field(ge=0)
    pages_processed: int = Field(ge=0)
    documents_prepared: int = Field(ge=0)
    eta_seconds: int | None = Field(default=None, ge=0)
    message: str


class MonitoringJobRead(BaseModel):
    id: UUID
    topic: str
    description: str | None = None
    status: JobStatus
    date_from_jalali: str
    date_to_jalali: str
    source_ids: list[UUID]
    bot_identity_id: UUID
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_message: str | None
    progress: MonitoringJobProgress | None = None
