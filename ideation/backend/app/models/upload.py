from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin

upload_categories = Table(
    "upload_categories",
    Base.metadata,
    Column(
        "upload_id",
        PG_UUID(as_uuid=True),
        ForeignKey("uploads.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "category_id",
        PG_UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Upload(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "uploads"
    __table_args__ = (
        CheckConstraint(
            "source IN ('web', 'bale', 'trend', 'dify_sync', 'legacy', 'haminyar_article')",
            name="ck_uploads_source",
        ),
    )

    uploaded_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id"),
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_filename: Mapped[str] = mapped_column(String(500))
    storage_path: Mapped[str] = mapped_column(Text)
    file_extension: Mapped[str] = mapped_column(String(20))
    mime_type: Mapped[str] = mapped_column(String(255))
    file_size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(50), default="uploaded", index=True)
    source: Mapped[str] = mapped_column(
        String(32), default="legacy", server_default="legacy", index=True
    )
    source_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    classification_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classification_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classification_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    classification_needs_review: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    dify_dataset_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    dify_document_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    uploaded_by_user = relationship("User", back_populates="uploads")
    categories = relationship("Category", secondary=upload_categories, back_populates="uploads")
    ingestion_jobs = relationship("IngestionJob", back_populates="upload")
    trend_imports = relationship("TrendImport", back_populates="upload")
