from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import Pagination, Principal, pagination, require_admin
from app.api.operations.identity import AdminUserOperations
from app.bootstrap.providers import provide_admin_user_operations
from app.schemas.admin import AdminUserCreate, AdminUserRead, AdminUserUpdate
from app.schemas.common import MessageResponse, PaginatedResponse

router = APIRouter(prefix="/admin/users", tags=["admin-users"])
AdminUsers = Annotated[AdminUserOperations, Depends(provide_admin_user_operations)]


@router.get("", response_model=PaginatedResponse[AdminUserRead])
async def list_users(
    _: Annotated[Principal, Depends(require_admin)],
    page_info: Annotated[Pagination, Depends(pagination)],
    operations: AdminUsers,
) -> PaginatedResponse[AdminUserRead]:
    return await operations.list(page_info.page, page_info.page_size)


@router.post("", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminUserCreate,
    _: Annotated[Principal, Depends(require_admin)],
    operations: AdminUsers,
) -> AdminUserRead:
    return await operations.create(payload)


@router.put("/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    _: Annotated[Principal, Depends(require_admin)],
    operations: AdminUsers,
) -> AdminUserRead:
    return await operations.update(user_id, payload)


@router.delete("/{user_id}", response_model=MessageResponse)
async def delete_user(
    user_id: UUID,
    _: Annotated[Principal, Depends(require_admin)],
    operations: AdminUsers,
) -> MessageResponse:
    await operations.delete(user_id)
    return MessageResponse(message="User deleted")
