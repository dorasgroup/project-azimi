from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class FrontendLogRequest(BaseModel):
    level: str = Field(pattern="^(info|warning|error)$")
    event_type: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=4000)
    metadata: dict | None = None


class SystemLogResponse(BaseModel):
    id: UUID
    level: str
    event_type: str
    actor_user_id: UUID | None
    request_id: str | None
    ip_address: str | None
    user_agent: str | None
    resource_type: str | None
    resource_id: UUID | None
    message: str
    metadata: dict | None
    created_at: datetime


class SystemLogListResponse(BaseModel):
    items: list[SystemLogResponse]
    page: int
    limit: int
    total: int
