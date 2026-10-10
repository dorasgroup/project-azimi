from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.shared.domain.logging import LogLevel


class SystemLogRead(BaseModel):
    id: UUID
    level: LogLevel
    module: str
    message: str
    context: dict[str, Any]
    request_id: UUID | None
    job_id: UUID | None
    created_at: datetime
