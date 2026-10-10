from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.dependencies import Principal, require_user
from app.api.operations.identity import AuthOperations
from app.bootstrap.providers import provide_auth_operations
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    TokenResponse,
    UserSummary,
)
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/auth", tags=["auth"])
Auth = Annotated[AuthOperations, Depends(provide_auth_operations)]


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, operations: Auth) -> TokenResponse:
    return await operations.login(payload.login, payload.password)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(payload: RefreshRequest, operations: Auth) -> TokenResponse:
    return await operations.refresh(payload.refresh_token)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    payload: LogoutRequest,
    _: Annotated[Principal, Depends(require_user)],
    operations: Auth,
) -> MessageResponse:
    await operations.logout(payload.refresh_token)
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserSummary)
async def me(
    principal: Annotated[Principal, Depends(require_user)], operations: Auth
) -> UserSummary:
    return await operations.me(UUID(principal.subject))


@router.put("/profile", response_model=UserSummary)
async def update_profile(
    payload: ProfileUpdateRequest,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Auth,
) -> UserSummary:
    return await operations.update_profile(UUID(principal.subject), payload)


@router.put("/change-password", response_model=MessageResponse, status_code=status.HTTP_200_OK)
async def change_password(
    payload: ChangePasswordRequest,
    principal: Annotated[Principal, Depends(require_user)],
    operations: Auth,
) -> MessageResponse:
    await operations.change_password(UUID(principal.subject), payload)
    return MessageResponse(message="Password changed")
