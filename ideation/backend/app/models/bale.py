from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class BaleConversation(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "bale_conversations"

    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    bale_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    chat_type: Mapped[str] = mapped_column(String(32), default="private", nullable=False)
    mode: Mapped[str] = mapped_column(String(32), default="menu", server_default="menu")
    intent_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pending_upload_update_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("bale_updates.update_id", ondelete="SET NULL"),
        nullable=True,
    )
    selected_category_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    client_session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    dify_conversation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    memory_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    selected_category = relationship("Category")


class BaleUpdate(Base, TimestampMixin):
    __tablename__ = "bale_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    bale_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    callback_query_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    callback_data: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    category_id_snapshot: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
    )
    client_session_id_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dify_conversation_id_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    intent_version_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    classification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_needs_review: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    document_file_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_file_unique_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    document_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploads.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
