"""Identity transport operations."""

from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.identity.domain.errors import AuthError, DuplicateUserError
from app.modules.identity.ports.operations import AdminUserOperationsPort, IdentityService
from app.schemas.admin import AdminUserCreate, AdminUserUpdate
from app.schemas.auth import ChangePasswordRequest, ProfileUpdateRequest
from app.shared.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


class AuthOperations:
    def __init__(self, service: IdentityService, transaction: TransactionRunner) -> None:
        self._service = service
        self._transaction = transaction

    async def login(self, login: str, password: str):
        try:
            return await self._transaction.run(lambda: self._service.authenticate(login, password))
        except AuthError as exc:
            raise AuthenticationError(str(exc)) from exc

    async def refresh(self, token: str):
        try:
            return await self._transaction.run(lambda: self._service.refresh(token))
        except AuthError as exc:
            raise AuthenticationError(str(exc)) from exc

    async def logout(self, token: str | None) -> None:
        await self._transaction.run(lambda: self._service.logout(token))

    async def me(self, user_id: UUID):
        summary = await self._service.get_active_user_summary(user_id)
        if summary is None:
            raise AuthenticationError("User is inactive")
        return summary

    async def update_profile(self, user_id: UUID, payload: ProfileUpdateRequest):
        try:
            return await self._transaction.run(
                lambda: self._service.update_profile(user_id, payload)
            )
        except DuplicateUserError as exc:
            raise ConflictError(str(exc)) from exc
        except AuthError as exc:
            raise NotFoundError(str(exc)) from exc

    async def change_password(self, user_id: UUID, payload: ChangePasswordRequest) -> None:
        try:
            await self._transaction.run(lambda: self._service.change_password(user_id, payload))
        except AuthError as exc:
            raise ValidationError(str(exc)) from exc


class AdminUserOperations:
    def __init__(self, service: AdminUserOperationsPort, transaction: TransactionRunner) -> None:
        self._service = service
        self._transaction = transaction

    async def list(self, page: int, page_size: int):
        return await self._service.list_users(page, page_size)

    async def create(self, payload: AdminUserCreate):
        try:
            return await self._transaction.run(lambda: self._service.create_user(payload))
        except DuplicateUserError as exc:
            raise ConflictError(str(exc)) from exc

    async def update(self, user_id: UUID, payload: AdminUserUpdate):
        try:
            result = await self._transaction.run(
                lambda: self._service.update_user(user_id, payload)
            )
        except DuplicateUserError as exc:
            raise ConflictError(str(exc)) from exc
        if result is None:
            raise NotFoundError("User not found")
        return result

    async def delete(self, user_id: UUID) -> None:
        deleted = await self._transaction.run(lambda: self._service.delete_user(user_id))
        if not deleted:
            raise NotFoundError("User not found")
