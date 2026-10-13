from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.base import TimestampMixin, UUIDPrimaryKeyMixin


class TrendImport(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "trend_imports"
    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "source_trend_id",
            "source_version",
            name="uq_trend_imports_source_trend",
        ),
    )

    source_system: Mapped[str] = mapped_column(String(64), index=True)
    source_trend_id: Mapped[str] = mapped_column(String(200))
    source_version: Mapped[int] = mapped_column(default=1)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    content_sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(500))
    topic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trend_status: Mapped[str] = mapped_column(String(50))
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    upload_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("uploads.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    delivery_status: Mapped[str] = mapped_column(
        String(50),
        default="received",
        index=True,
    )
    dify_status: Mapped[str] = mapped_column(
        String(50),
        default="not_started",
        index=True,
    )
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dify_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    upload = relationship("Upload", back_populates="trend_imports")
