from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.monitoring_job import MonitoringJob
    from app.models.source import Source


class CrawledDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "crawled_documents"
    __table_args__ = (
        UniqueConstraint("job_id", "url", name="uq_crawled_documents_job_url"),
        CheckConstraint(
            "matched_score IS NULL OR (matched_score >= 0 AND matched_score <= 1)",
            name="matched_score_range",
        ),
        CheckConstraint(
            "published_at_confidence IS NULL OR "
            "(published_at_confidence >= 0 AND published_at_confidence <= 1)",
            name="published_at_confidence_range",
        ),
    )

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitoring_jobs.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), index=True)
    title: Mapped[str | None] = mapped_column(String(1000))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at_source: Mapped[str | None] = mapped_column(String(64))
    published_at_confidence: Mapped[float | None] = mapped_column(Float)
    published_at_raw: Mapped[str | None] = mapped_column(String(255))
    raw_html: Mapped[str | None] = mapped_column(Text)
    clean_text: Mapped[str | None] = mapped_column(Text)
    markdown: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(16))
    matched_score: Mapped[float | None] = mapped_column(Float)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    job: Mapped[MonitoringJob] = relationship(back_populates="documents")
    source: Mapped[Source] = relationship(back_populates="documents")
