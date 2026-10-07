"""Source transport operations."""

from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.crawling.domain.errors import DuplicateSourceError
from app.modules.crawling.ports.operations import SourceCommands, SourceQueries
from app.schemas.source import SourceCreate, SourceUpdate
from app.shared.domain.errors import ConflictError, NotFoundError


class SourceOperations:
    def __init__(
        self,
        queries: SourceQueries,
        commands: SourceCommands,
        transaction: TransactionRunner,
    ) -> None:
        self._queries = queries
        self._commands = commands
        self._transaction = transaction

    async def list(self, page: int, page_size: int, *, operational_only: bool = False):
        return await self._queries.list_sources(page, page_size, operational_only=operational_only)

    async def create(self, payload: SourceCreate, creator_id: UUID):
        try:
            return await self._transaction.run(
                lambda: self._commands.create_source(payload, creator_id)
            )
        except DuplicateSourceError as exc:
            raise ConflictError(str(exc)) from exc

    async def update(self, source_id: UUID, payload: SourceUpdate):
        try:
            result = await self._transaction.run(
                lambda: self._commands.update_source(source_id, payload)
            )
        except DuplicateSourceError as exc:
            raise ConflictError(str(exc)) from exc
        if result is None:
            raise NotFoundError("Source not found")
        return result

    async def delete(self, source_id: UUID) -> None:
        deleted = await self._transaction.run(lambda: self._commands.delete_source(source_id))
        if not deleted:
            raise NotFoundError("Source not found")

    async def check(self, source_id: UUID):
        result = await self._transaction.run(lambda: self._commands.check_source(source_id))
        if result is None:
            raise NotFoundError("Source not found")
        return result
