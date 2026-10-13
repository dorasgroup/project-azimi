from pydantic import BaseModel, EmailStr
from uuid import UUID
from datetime import datetime

class UserResponseDTO(BaseModel):
    id: UUID
    email: str
    full_name: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class CreateUserDTO(BaseModel):
    email: EmailStr
    password: str
    full_name: str
