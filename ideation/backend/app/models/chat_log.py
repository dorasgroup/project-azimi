from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class ChatLog(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "chat_logs"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("users.id"), index=True)
    client_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    dify_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
    category_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    user = relationship("User", back_populates="chat_logs")
