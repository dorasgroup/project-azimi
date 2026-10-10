from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import Pagination, Principal, pagination, require_admin, require_user
from app.api.operations.bot_identities import BotIdentityOperations
from app.bootstrap.providers import provide_bot_identity_operations
from app.schemas.bot_identity import (
    ActiveBotIdentityRequest,
    BotIdentityCreate,
    BotIdentityRead,
    BotIdentityUpdate,
)
from app.schemas.common import MessageResponse, PaginatedResponse

router = APIRouter(prefix="/bot-identities", tags=["bot-identities"])
BotIdentities = Annotated[BotIdentityOperations, Depends(provide_bot_identity_operations)]


@router.get("", response_model=PaginatedResponse[BotIdentityRead])
async def list_identities(
    _: Annotated[Principal, Depends(require_user)],
    page_info: Annotated[Pagination, Depends(pagination)],
    operations: BotIdentities,
) -> PaginatedResponse[BotIdentityRead]:
    return await operations.list(page_info.page, page_info.page_size)


@router.post("", response_model=BotIdentityRead, status_code=status.HTTP_201_CREATED)
async def create_identity(
    payload: BotIdentityCreate,
    principal: Annotated[Principal, Depends(require_admin)],
    operations: BotIdentities,
) -> BotIdentityRead:
    return await operations.create(payload, UUID(principal.subject))


@router.get("/active", response_model=BotIdentityRead)
async def active_identity(
    _: Annotated[Principal, Depends(require_user)], operations: BotIdentities
) -> BotIdentityRead:
    return await operations.active()


@router.put("/active", response_model=BotIdentityRead)
async def set_active_identity(
    payload: ActiveBotIdentityRequest,
    _: Annotated[Principal, Depends(require_admin)],
    operations: BotIdentities,
) -> BotIdentityRead:
    return await operations.set_active(payload.identity_id)


@router.put("/{identity_id}", response_model=BotIdentityRead)
async def update_identity(
    identity_id: UUID,
    payload: BotIdentityUpdate,
    _: Annotated[Principal, Depends(require_admin)],
    operations: BotIdentities,
) -> BotIdentityRead:
    return await operations.update(identity_id, payload)


@router.delete("/{identity_id}", response_model=MessageResponse)
async def delete_identity(
    identity_id: UUID,
    _: Annotated[Principal, Depends(require_admin)],
    operations: BotIdentities,
) -> MessageResponse:
    await operations.delete(identity_id)
    return MessageResponse(message="Bot identity deleted")
