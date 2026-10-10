from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import Pagination, Principal, pagination, require_admin, require_user
from app.api.operations.sources import SourceOperations
from app.bootstrap.providers import provide_source_operations
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.source import SourceCheckResponse, SourceCreate, SourceRead, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])
Sources = Annotated[SourceOperations, Depends(provide_source_operations)]


@router.get("", response_model=PaginatedResponse[SourceRead])
async def list_sources(
    _: Annotated[Principal, Depends(require_user)],
    page_info: Annotated[Pagination, Depends(pagination)],
    operations: Sources,
    operational_only: bool = False,
) -> PaginatedResponse[SourceRead]:
    return await operations.list(page_info.page, page_info.page_size, operational_only=operational_only)


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate,
    principal: Annotated[Principal, Depends(require_admin)],
    operations: Sources,
) -> SourceRead:
    return await operations.create(payload, UUID(principal.subject))


@router.put("/{source_id}", response_model=SourceRead)
async def update_source(
    source_id: UUID,
    payload: SourceUpdate,
    _: Annotated[Principal, Depends(require_admin)],
    operations: Sources,
) -> SourceRead:
    return await operations.update(source_id, payload)


@router.delete("/{source_id}", response_model=MessageResponse)
async def delete_source(
    source_id: UUID,
    _: Annotated[Principal, Depends(require_admin)],
    operations: Sources,
) -> MessageResponse:
    await operations.delete(source_id)
    return MessageResponse(message="Source deleted")


@router.post("/{source_id}/check", response_model=SourceCheckResponse)
async def check_source(
    source_id: UUID,
    _: Annotated[Principal, Depends(require_admin)],
    operations: Sources,
) -> SourceCheckResponse:
    return await operations.check(source_id)
