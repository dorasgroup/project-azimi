from uuid import uuid4

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_current_user
from app.modules.documents.application.dto import (
    DocumentResponseDTO,
    FinalizeUploadRequestDTO,
    UploadSessionResponseDTO,
)
from app.modules.documents.application.use_cases import FinalizeUploadUseCase
from app.schemas.auth import CurrentUserResponse
from app.shared.application.dto import StandardResponse
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.presentation.dependencies import get_uow

router = APIRouter(prefix="/api/v1/documents", tags=["Documents"])

@router.post("/upload-sessions", response_model=StandardResponse[UploadSessionResponseDTO])
async def create_upload_session(
    request: Request,
    _current_user: CurrentUserResponse = Depends(get_current_user),
):
    # Mocking Presigned URL generation for Object Storage
    storage_key = f"uploads/{uuid4()}"
    presigned_url = f"https://mock-s3.example.com/{storage_key}?signature=mock"
    
    return StandardResponse.ok(
        data=UploadSessionResponseDTO(presigned_url=presigned_url, storage_key=storage_key),
        message="Upload session created.",
        trace_id=request.headers.get("X-Request-ID", "")
    )

@router.post("/finalize", response_model=StandardResponse[DocumentResponseDTO], status_code=201)
async def finalize_upload(
    req: FinalizeUploadRequestDTO,
    request: Request,
    current_user: CurrentUserResponse = Depends(get_current_user),
    uow: IUnitOfWork = Depends(get_uow),
):
    use_case = FinalizeUploadUseCase(uow)
    doc_dto = await use_case.execute(current_user.id, req)
    
    return StandardResponse.ok(
        data=doc_dto,
        message="Upload finalized, document is pending processing.",
        trace_id=request.headers.get("X-Request-ID", "")
    )
