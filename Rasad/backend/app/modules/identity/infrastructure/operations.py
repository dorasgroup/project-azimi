from __future__ import annotations

from datetime import UTC, datetime
from math import ceil
from uuid import UUID

from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    refresh_token_expires_at,
    verify_password,
)
from app.models import RefreshToken, User
from app.modules.identity.ports import UserRepository
from app.schemas.admin import AdminUserCreate, AdminUserUpdate
from app.schemas.auth import ChangePasswordRequest, ProfileUpdateRequest, TokenResponse, UserSummary
from app.schemas.common import PageMeta, PaginatedResponse
from app.shared.domain.errors import ConflictError


def user_to_summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        full_name=user.full_name,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
    )


from app.modules.identity.domain.errors import AuthError, DuplicateUserError


class IdentityOperationsAdapter:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    async def authenticate(self, login: str, password: str) -> TokenResponse:
        user = await self._repository.find_login(login)
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            raise AuthError("Invalid username/email or password")
        return await self.issue_tokens(user)

    async def issue_tokens(self, user: User) -> TokenResponse:
        access_token, expires_in = create_access_token(str(user.id), user.role)
        refresh_token = create_refresh_token()
        await self._repository.add(
            RefreshToken(
                user_id=user.id,
                token_hash=hash_token(refresh_token),
                expires_at=refresh_token_expires_at(),
            )
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    async def refresh(self, refresh_token: str) -> TokenResponse:
        token_hash = hash_token(refresh_token)
        token = await self._repository.refresh(token_hash)
        if (
            token is None
            or token.revoked_at is not None
            or token.expires_at <= datetime.now(UTC)
            or token.user is None
            or not token.user.is_active
        ):
            raise AuthError("Invalid refresh token")
        token.revoked_at = datetime.now(UTC)
        return await self.issue_tokens(token.user)

    async def logout(self, refresh_token: str | None = None) -> None:
        if not refresh_token:
            return
        token = await self._repository.refresh_by_hash(hash_token(refresh_token))
        if token and token.revoked_at is None:
            token.revoked_at = datetime.now(UTC)
            await self._repository.flush()

    async def get_active_user(self, user_id: UUID) -> User | None:
        user = await self._repository.get(user_id)
        if user is None or not user.is_active:
            return None
        return user

    async def get_active_user_summary(self, user_id: UUID) -> UserSummary | None:
        user = await self.get_active_user(user_id)
        if user is None:
            return None
        return user_to_summary(user)

    async def update_profile(self, user_id: UUID, payload: ProfileUpdateRequest) -> UserSummary:
        user = await self.get_active_user(user_id)
        if user is None:
            raise AuthError("User not found")
        user.full_name = payload.full_name
        user.email = str(payload.email)
        await self._safe_flush()
        return user_to_summary(user)

    async def change_password(self, user_id: UUID, payload: ChangePasswordRequest) -> None:
        user = await self.get_active_user(user_id)
        if user is None or not verify_password(payload.current_password, user.password_hash):
            raise AuthError("Invalid current password")
        user.password_hash = hash_password(payload.new_password)
        await self._repository.flush()

    async def _safe_flush(self) -> None:
        try:
            await self._repository.flush()
        except ConflictError as exc:
            raise DuplicateUserError(str(exc)) from exc


class AdminUserOperationsAdapter:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    async def list_users(self, page: int, page_size: int) -> PaginatedResponse[UserSummary]:
        users, total = await self._repository.list(page, page_size)
        return PaginatedResponse(
            items=[user_to_summary(user) for user in users],
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def create_user(self, payload: AdminUserCreate) -> UserSummary:
        user = User(
            full_name=payload.full_name,
            username=payload.username,
            email=str(payload.email),
            password_hash=hash_password(payload.password),
            role=payload.role,
            is_active=payload.is_active,
        )
        try:
            await self._repository.add(user)
        except ConflictError as exc:
            raise DuplicateUserError(str(exc)) from exc
        return user_to_summary(user)

    async def update_user(self, user_id: UUID, payload: AdminUserUpdate) -> UserSummary | None:
        user = await self._repository.get(user_id)
        if user is None:
            return None
        user.full_name = payload.full_name
        user.email = str(payload.email)
        user.role = payload.role
        user.is_active = payload.is_active
        await self._safe_flush()
        return user_to_summary(user)

    async def delete_user(self, user_id: UUID) -> bool:
        user = await self._repository.get(user_id)
        if user is None:
            return False
        await self._repository.delete(user)
        return True

    async def _safe_flush(self) -> None:
        try:
            await self._repository.flush()
        except ConflictError as exc:
            raise DuplicateUserError(str(exc)) from exc
