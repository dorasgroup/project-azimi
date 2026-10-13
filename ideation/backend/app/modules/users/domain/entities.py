from uuid import UUID
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class User:
    id: UUID
    email: str
    password_hash: str
    full_name: str
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    def activate(self):
        self.is_active = True

    def deactivate(self):
        self.is_active = False

@dataclass
class Role:
    id: UUID
    name: str
    description: Optional[str]

@dataclass
class Permission:
    id: UUID
    name: str  # e.g., "users.read"
