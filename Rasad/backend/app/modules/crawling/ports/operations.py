"""Crawling operations ports / protocols."""

from typing import Protocol
from uuid import UUID

from app.schemas.common import PaginatedResponse
from app.schemas.source import SourceCreate, SourceRead, SourceUpdate


class SourceQueries(Protocol):
    async def list_sources(self, page: int, page_size: int, *, operational_only: bool = False) -> PaginatedResponse[SourceRead]: ...


class SourceCommands(Protocol):
    async def create_source(self, payload: SourceCreate, creator_id: UUID) -> SourceRead: ...
    async def update_source(self, source_id: UUID, payload: SourceUpdate) -> SourceRead | None: ...
    async def delete_source(self, source_id: UUID) -> bool: ...
    async def check_source(self, source_id: UUID) -> dict[str, object] | None: ...
