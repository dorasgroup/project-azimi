from app.shared.application.unit_of_work import IUnitOfWork
from app.modules.documents.application.dto import FinalizeUploadRequestDTO, DocumentResponseDTO
from app.modules.documents.infrastructure.models import DocumentModel
from app.modules.jobs.infrastructure.models import ProcessingJobModel, OutboxMessageModel
from app.modules.documents.domain.entities import DocumentStatus
from app.modules.jobs.domain.entities import JobStatus
from uuid import uuid4
from datetime import datetime, timezone

class FinalizeUploadUseCase:
    def __init__(self, uow: IUnitOfWork):
        self.uow = uow

    async def execute(self, owner_id: str, req: FinalizeUploadRequestDTO) -> DocumentResponseDTO:
        async with self.uow:
            # 1. Create Document Entity (Mapped to Model directly for simplicity in this phase)
            doc_id = uuid4()
            document = DocumentModel(
                id=doc_id,
                owner_id=owner_id,
                category_id=req.category_id,
                file_name=req.file_name,
                storage_key=req.storage_key,
                status=DocumentStatus.PENDING,
                created_at=datetime.now(timezone.utc)
            )

            # 2. Create Processing Job
            job = ProcessingJobModel(
                id=uuid4(),
                document_id=doc_id,
                status=JobStatus.CREATED,
                created_at=datetime.now(timezone.utc)
            )

            # 3. Create Outbox Message (Transactional Outbox Pattern)
            outbox_msg = OutboxMessageModel(
                id=uuid4(),
                event_type="document.processing.requested",
                payload={"document_id": str(doc_id), "storage_key": req.storage_key},
                is_published=False,
                created_at=datetime.now(timezone.utc)
            )

            # Save all in a single transaction
            self.uow.session.add(document)
            self.uow.session.add(job)
            self.uow.session.add(outbox_msg)

            await self.uow.commit()

            return DocumentResponseDTO(
                id=document.id,
                owner_id=document.owner_id,
                category_id=document.category_id,
                file_name=document.file_name,
                storage_key=document.storage_key,
                status=document.status.value,
                created_at=document.created_at
            )
