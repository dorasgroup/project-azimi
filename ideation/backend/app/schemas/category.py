from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CategoryResponse(BaseModel):
    id: UUID
    name: str
    available_in_bale: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


class CategoryBaleAvailabilityUpdate(BaseModel):
    available_in_bale: bool
