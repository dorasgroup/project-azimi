"""Typed document-processing boundary contracts."""

from typing import Protocol, runtime_checkable
from uuid import UUID

from app.modules.documents.domain import (
    DocumentPreparationOutcome,
    DocumentPreparationRequest,
    PreparedDocumentRecord,
)


@runtime_checkable
class DocumentRepository(Protocol):
    async def replace_for_source(
        self, job_id: UUID, source_id: UUID, documents: list[PreparedDocumentRecord]
    ) -> int: ...

    async def source_counts(self, job_id: UUID) -> dict[UUID, int]: ...

    async def clear_for_job(self, job_id: UUID) -> None: ...


class DocumentProcessor(Protocol):
    def prepare(self, request: DocumentPreparationRequest) -> DocumentPreparationOutcome: ...
