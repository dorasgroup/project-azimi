from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_non_guest
from app.db.session import get_db_session
from app.ingestion.service import (
    IngestionJobNotFound,
    InvalidIngestionTransition,
    get_job_status,
    retry_owned_job,
)
from app.ingestion.state import ACTIVE_JOB_STATUSES, JobStatus
from app.repositories.upload_repository import UploadRepository
from app.schemas.auth import CurrentUserResponse
from app.schemas.upload import IngestionJobResponse, UploadListItem, UploadResponse
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import (
    DifyDocumentDeleteError,
    UploadBusyError,
    UploadOrigin,
    UploadService,
)

router = APIRouter()


class UpdateCategoriesRequest(BaseModel):
    category_ids: list[UUID]


@router.post(
    "",
    response_model=UploadResponse,
    responses={status.HTTP_202_ACCEPTED: {"model": UploadResponse}},
)
async def upload_file(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    category_ids: list[str] = Form(None),
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> UploadResponse:
    try:
        uuids = []
        if category_ids:
            for cid in category_ids:
                if not cid:
                    continue
                if "," in cid:
                    for sub_cid in cid.split(","):
                        sub_cid_clean = sub_cid.strip()
                        if sub_cid_clean:
                            uuids.append(UUID(sub_cid_clean))
                else:
                    uuids.append(UUID(cid.strip()))
        result = await UploadService(session).create_upload(
            UUID(current_user.id),
            file,
            uuids,
            request_id=request.state.request_id,
            origin=UploadOrigin(source="web"),
        )
        if result.ingestion_job_id and result.status in {
            str(item) for item in ACTIVE_JOB_STATUSES | {JobStatus.ENQUEUE_FAILED}
        }:
            response.status_code = status.HTTP_202_ACCEPTED
        return result
    except FileValidationError as exc:
        status_code = status.HTTP_400_BAD_REQUEST
        if exc.code == "FILE_TOO_LARGE":
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
        elif exc.code == "UNSUPPORTED_FILE_TYPE":
            status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        elif exc.code == "FILE_TOO_SMALL":
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": exc.message},
        ) from exc


@router.get("")
async def list_uploads(
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    user_id = UUID(current_user.id)
    is_super_admin = current_user.role == "super_admin"
    if is_super_admin:
        await UploadService(session).sync_with_dify(user_id)
    uploads = await UploadRepository(session).list_all(
        uploaded_by=None if is_super_admin else user_id,
        exclude_trend_imports=True,
    )
    items = [
        UploadListItem(
            id=str(upload.id),
            original_filename=upload.original_filename,
            file_size_bytes=upload.file_size_bytes,
            mime_type=upload.mime_type,
            status=upload.status,
            source=upload.source,
            classification_method=upload.classification_method,
            classification_score=upload.classification_score,
            classification_needs_review=upload.classification_needs_review,
            categories=[
                {"id": cat.id, "name": cat.name}
                for cat in upload.categories
            ],
            created_at=upload.created_at,
        ).model_dump(mode="json")
        for upload in uploads
    ]
    return {"items": items, "page": 1, "limit": 100, "total": len(items)}


@router.delete("/{upload_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    upload_id: UUID,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    try:
        await UploadService(session).delete_upload(
            upload_id,
            UUID(current_user.id),
            allow_all=current_user.role == "super_admin",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload not found",
        ) from exc
    except DifyDocumentDeleteError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File was not deleted because Dify document deletion failed",
        ) from exc
    except UploadBusyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="File cannot be deleted while ingestion is active",
        ) from exc


@router.get("/{upload_id}/ingestion", response_model=IngestionJobResponse)
async def ingestion_status(
    upload_id: UUID,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> IngestionJobResponse:
    try:
        return await get_job_status(
            session,
            upload_id,
            UUID(current_user.id),
            allow_all=current_user.role == "super_admin",
        )
    except IngestionJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion job not found",
        ) from exc


@router.post(
    "/{upload_id}/ingestion/retry",
    response_model=IngestionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_ingestion(
    upload_id: UUID,
    request: Request,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> IngestionJobResponse:
    try:
        return await retry_owned_job(
            session,
            upload_id,
            UUID(current_user.id),
            request.state.request_id,
            allow_all=current_user.role == "super_admin",
        )
    except IngestionJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ingestion job not found",
        ) from exc
    except InvalidIngestionTransition as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This ingestion job cannot be retried in its current state",
        ) from exc


@router.put("/{upload_id}/categories")
async def update_upload_categories(
    upload_id: UUID,
    req: UpdateCategoriesRequest,
    current_user: CurrentUserResponse = Depends(require_non_guest),
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    try:
        await UploadService(session).update_upload_categories(
            upload_id,
            req.category_ids,
            UUID(current_user.id),
            allow_all=current_user.role == "super_admin",
        )
        return {"status": "success"}
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Upload not found",
        ) from exc
