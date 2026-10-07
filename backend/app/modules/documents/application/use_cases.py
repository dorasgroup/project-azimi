"""Single-purpose use cases for document preparation and persistence."""

from uuid import UUID

from app.modules.documents.domain import (
    DocumentPreparationOutcome,
    DocumentPreparationRequest,
    PreparedDocumentRecord,
)
from app.modules.documents.ports import DocumentProcessor, DocumentRepository


class PrepareDocuments:
    def __init__(self, processor: DocumentProcessor) -> None:
        self._processor = processor

    def execute(self, request: DocumentPreparationRequest) -> DocumentPreparationOutcome:
        return self._processor.prepare(request)


class PersistPreparedDocuments:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(
        self, job_id: UUID, source_id: UUID, documents: list[PreparedDocumentRecord]
    ) -> int:
        return await self._repository.replace_for_source(job_id, source_id, documents)


class GetSourceDocumentCounts:
    def __init__(self, repository: DocumentRepository) -> None:
        self._repository = repository

    async def execute(self, job_id: UUID) -> dict[UUID, int]:
        return await self._repository.source_counts(job_id)
