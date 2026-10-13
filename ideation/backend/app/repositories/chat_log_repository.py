from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_log import ChatLog


class ChatLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, chat_log: ChatLog) -> ChatLog:
        self.session.add(chat_log)
        await self.session.flush()
        return chat_log

