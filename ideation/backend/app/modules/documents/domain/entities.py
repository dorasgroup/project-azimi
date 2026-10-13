from uuid import UUID
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from enum import Enum

class DocumentStatus(str, Enum):
    PENDING = "PENDING"
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY = "READY"
    FAILED = "FAILED"

@dataclass
class Document:
    id: UUID
    owner_id: UUID
    category_id: Optional[UUID]
    file_name: str
    storage_key: str
    status: DocumentStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None

    def mark_as_uploaded(self):
        if self.status != DocumentStatus.PENDING:
            raise ValueError("Cannot finalize upload for non-pending document.")
        self.status = DocumentStatus.UPLOADED

    def soft_delete(self):
        self.deleted_at = datetime.utcnow()
