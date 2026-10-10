"""Persistence contracts owned by the Identity module."""

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID


class UserRecord(Protocol):
    id: UUID
    full_name: str
    username: str
    email: str
    password_hash: str
    role: str
    is_active: bool


class RefreshTokenRecord(Protocol):
    token_hash: str
    expires_at: datetime
    revoked_at: datetime | None
    user: UserRecord | None


@runtime_checkable
class UserRepository(Protocol):
    async def find_login(self, login: str) -> UserRecord | None: ...

    async def get(self, user_id: UUID) -> UserRecord | None: ...

    async def refresh(self, token_hash: str) -> RefreshTokenRecord | None: ...

    async def refresh_by_hash(self, token_hash: str) -> RefreshTokenRecord | None: ...

    async def list(self, page: int, page_size: int) -> tuple[list[UserRecord], int]: ...

    async def add(self, entity: UserRecord | RefreshTokenRecord) -> None: ...

    async def delete(self, user: UserRecord) -> None: ...

    async def flush(self) -> None: ...

from app.modules.identity.ports.operations import AdminUserOperationsPort, IdentityService
