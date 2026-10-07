from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SourceCrawlStatus, SourceStatus, SourceType

if TYPE_CHECKING:
    from app.models.crawled_document import CrawledDocument
    from app.models.monitoring_job import MonitoringJob
    from app.models.user import User


class Source(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(
            "last_response_time_ms IS NULL OR last_response_time_ms >= 0",
            name="nonnegative_response_time",
        ),
    )

    title: Mapped[str] = mapped_column(String(250), nullable=False)
    base_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    type: Mapped[SourceType] = mapped_column(
        Enum(
            SourceType,
            name="source_type",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    crawl_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_status: Mapped[SourceStatus] = mapped_column(
        Enum(
            SourceStatus,
            name="source_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceStatus.UNKNOWN,
        server_default=SourceStatus.UNKNOWN.value,
    )
    last_status_message: Mapped[str | None] = mapped_column(Text)
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_response_time_ms: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_crawl_status: Mapped[SourceCrawlStatus] = mapped_column(
        Enum(
            SourceCrawlStatus,
            name="source_crawl_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceCrawlStatus.UNKNOWN,
        server_default=SourceCrawlStatus.UNKNOWN.value,
    )
    last_crawl_message: Mapped[str | None] = mapped_column(Text)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    creator: Mapped[User] = relationship(back_populates="sources")
    monitoring_jobs: Mapped[list[MonitoringJob]] = relationship(
        secondary="monitoring_job_sources",
        back_populates="sources",
    )
    documents: Mapped[list[CrawledDocument]] = relationship(back_populates="source")
