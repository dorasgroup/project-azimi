"""Bot-identity transport operations."""

from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.monitoring.domain.errors import BotIdentityInUseError
from app.modules.monitoring.ports.operations import (
    BotIdentityCommands,
    BotIdentityQueries,
)
from app.schemas.bot_identity import BotIdentityCreate, BotIdentityUpdate
from app.shared.domain.errors import ConflictError, NotFoundError


class BotIdentityOperations:
    def __init__(
        self,
        queries: BotIdentityQueries,
        commands: BotIdentityCommands,
        transaction: TransactionRunner,
    ) -> None:
        self._queries = queries
        self._commands = commands
        self._transaction = transaction

    async def list(self, page: int, page_size: int):
        return await self._queries.list_identities(page, page_size)

    async def create(self, payload: BotIdentityCreate, creator_id: UUID):
        return await self._transaction.run(
            lambda: self._commands.create_identity(payload, creator_id)
        )

    async def active(self):
        result = await self._queries.active_identity()
        if result is None:
            raise NotFoundError("Active bot identity not found")
        return result

    async def set_active(self, identity_id: UUID):
        result = await self._transaction.run(
            lambda: self._commands.set_active_identity(identity_id)
        )
        if result is None:
            raise NotFoundError("Active bot identity not found")
        return result

    async def update(self, identity_id: UUID, payload: BotIdentityUpdate):
        result = await self._transaction.run(
            lambda: self._commands.update_identity(identity_id, payload)
        )
        if result is None:
            raise NotFoundError("Bot identity not found")
        return result

    async def delete(self, identity_id: UUID) -> None:
        try:
            deleted = await self._transaction.run(
                lambda: self._commands.delete_identity(identity_id)
            )
        except BotIdentityInUseError as exc:
            raise ConflictError(str(exc)) from exc
        if not deleted:
            raise NotFoundError("Bot identity not found")
