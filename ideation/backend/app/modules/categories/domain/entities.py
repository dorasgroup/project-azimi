from uuid import UUID
from dataclasses import dataclass
from typing import Optional
from datetime import datetime

@dataclass
class Category:
    id: UUID
    name: str
    parent_id: Optional[UUID]
    created_at: datetime
    updated_at: Optional[datetime] = None

    def rename(self, new_name: str):
        if not new_name or len(new_name) > 100:
            raise ValueError("Category name must be between 1 and 100 characters.")
        self.name = new_name
