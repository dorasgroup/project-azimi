from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class BotIdentityCreate(BaseModel):
    title: str = Field(min_length=1, max_length=250)
    description: str | None = None
    role_definition: str = Field(min_length=1)
    tone: str | None = None
    analysis_style: str | None = None
    cognitive_framework: str | None = None
    output_structure: dict[str, Any] = Field(default_factory=dict)
    source_policy: dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True


class BotIdentityUpdate(BotIdentityCreate):
    pass


class BotIdentityRead(BotIdentityCreate):
    id: UUID
    is_default: bool


class ActiveBotIdentityRequest(BaseModel):
    identity_id: UUID
