from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.modules.identity.domain.roles import UserRole


class AdminUserCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    username: str = Field(min_length=3, max_length=100)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole = UserRole.USER
    is_active: bool = True


class AdminUserUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    role: UserRole
    is_active: bool


class AdminUserRead(BaseModel):
    id: UUID
    full_name: str
    username: str
    email: str
    role: UserRole
    is_active: bool
