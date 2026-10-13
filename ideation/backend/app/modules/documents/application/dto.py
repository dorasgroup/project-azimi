from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Optional

class FinalizeUploadRequestDTO(BaseModel):
    category_id: Optional[UUID] = None
    file_name: str
    storage_key: str

class DocumentResponseDTO(BaseModel):
    id: UUID
    owner_id: UUID
    category_id: Optional[UUID]
    file_name: str
    storage_key: str
    status: str
    created_at: datetime
    
    class Config:
        from_attributes = True

class UploadSessionResponseDTO(BaseModel):
    presigned_url: str
    storage_key: str
