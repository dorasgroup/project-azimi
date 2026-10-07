from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.modules.identity.domain.roles import UserRole


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=32)


class LogoutRequest(BaseModel):
    refresh_token: str | None = None


class UserSummary(BaseModel):
    id: UUID
    full_name: str
    username: str
    email: str
    role: UserRole
    is_active: bool


class ProfileUpdateRequest(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=8, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
