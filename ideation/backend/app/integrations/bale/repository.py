from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bale import BaleConversation, BaleUpdate


class BaleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_update(self, values: dict) -> tuple[BaleUpdate, bool]:
        statement = (
            insert(BaleUpdate)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[BaleUpdate.update_id])
        )
        result = await self.session.execute(statement)
        update = await self.session.get(BaleUpdate, values["update_id"])
        if update is None:
            raise RuntimeError("Bale update could not be persisted")
        return update, result.rowcount == 0

    async def get_update(self, update_id: int) -> BaleUpdate | None:
        return await self.session.get(BaleUpdate, update_id)

    async def get_conversation(self, chat_id: int) -> BaleConversation | None:
        result = await self.session.execute(
            select(BaleConversation).where(BaleConversation.chat_id == chat_id)
        )
        return result.scalar_one_or_none()

    async def get_or_create_conversation(
        self,
        *,
        chat_id: int,
        bale_user_id: int,
        chat_type: str,
    ) -> BaleConversation:
        statement = (
            insert(BaleConversation)
            .values(
                chat_id=chat_id,
                bale_user_id=bale_user_id,
                chat_type=chat_type,
                client_session_id=f"bale-{uuid4()}",
            )
            .on_conflict_do_nothing(index_elements=[BaleConversation.chat_id])
        )
        await self.session.execute(statement)
        conversation = await self.get_conversation(chat_id)
        if conversation is None:
            raise RuntimeError("Bale conversation could not be persisted")
        conversation.bale_user_id = bale_user_id
        conversation.chat_type = chat_type
        return conversation

    @staticmethod
    def select_category(conversation: BaleConversation, category_id: UUID) -> None:
        conversation.selected_category_id = category_id
        conversation.client_session_id = f"bale-{uuid4()}"
        conversation.dify_conversation_id = None
        conversation.memory_expires_at = None
        conversation.mode = "chat"
        conversation.intent_version += 1

    @staticmethod
    def set_mode(conversation: BaleConversation, mode: str) -> None:
        if conversation.mode != mode:
            conversation.mode = mode
            conversation.intent_version += 1

    @staticmethod
    def reset_conversation(conversation: BaleConversation) -> None:
        conversation.client_session_id = f"bale-{uuid4()}"
        conversation.dify_conversation_id = None
        conversation.memory_expires_at = None
        conversation.intent_version += 1

    @staticmethod
    def expire_memory(conversation: BaleConversation) -> None:
        conversation.client_session_id = f"bale-{uuid4()}"
        conversation.dify_conversation_id = None
        conversation.memory_expires_at = None
        conversation.intent_version += 1
