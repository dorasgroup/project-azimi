from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class SiteArticle(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One public CMS article, with at most one serving and one pending revision."""

    __tablename__ = "site_articles"

    external_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1000))
    source_url: Mapped[str] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=0)
    active_upload_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="RESTRICT"), index=True
    )
    pending_upload_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="RESTRICT"), index=True
    )
    retired_upload_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("uploads.id", ondelete="RESTRICT")
    )
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dify_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)


class SiteArticleSyncState(Base):
    __tablename__ = "site_article_sync_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_article_count: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
