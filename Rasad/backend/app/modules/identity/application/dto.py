"""Transport- and persistence-independent identity DTOs."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class AuthenticateUserCommand:
    login: str
    password: str


@dataclass(frozen=True)
class CreateUserCommand:
    full_name: str
    username: str
    email: str
    password: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class UpdateUserCommand:
    full_name: str
    email: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class UserResult:
    id: UUID
    full_name: str
    username: str
    email: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class TokenResult:
    access_token: str
    refresh_token: str
    expires_in: int
