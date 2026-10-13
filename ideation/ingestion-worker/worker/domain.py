from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

class StorageReference(BaseModel):
    provider: str
    bucket: str
    objectKey: str
    checksum: Optional[str] = None

class ProcessingPolicy(BaseModel):
    pipelineVersion: str
    language: str
    chunkingPolicy: str

class JobData(BaseModel):
    jobId: UUID
    documentId: UUID
    tenantId: UUID
    storage: StorageReference
    processing: ProcessingPolicy

class IngestionJobEvent(BaseModel):
    eventId: str
    eventType: str
    eventVersion: int
    occurredAt: datetime
    correlationId: str
    data: JobData

class DocumentChunk(BaseModel):
    chunk_id: UUID
    document_id: UUID
    sequence: int
    text: str
    token_count: int
    metadata: dict
