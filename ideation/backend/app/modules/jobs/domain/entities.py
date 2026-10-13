from uuid import UUID
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum

class JobStatus(str, Enum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"

@dataclass
class ProcessingJob:
    id: UUID
    document_id: UUID
    status: JobStatus
    retry_count: int
    last_error: Optional[str]
    created_at: datetime
    updated_at: Optional[datetime] = None

    def mark_as_queued(self):
        if self.status != JobStatus.CREATED and self.status != JobStatus.RETRY_SCHEDULED:
            raise ValueError("Invalid state transition to QUEUED")
        self.status = JobStatus.QUEUED

@dataclass
class OutboxMessage:
    id: UUID
    event_type: str
    payload: Dict[str, Any]
    is_published: bool
    created_at: datetime
    published_at: Optional[datetime] = None

    def mark_as_published(self):
        self.is_published = True
        self.published_at = datetime.utcnow()
