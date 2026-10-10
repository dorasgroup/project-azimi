from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.crawled_document import CrawledDocument
    from app.models.monitoring_run import MonitoringRun
    from app.models.source import Source
    from app.models.source_task import SourceTask


class SourceTaskDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_task_documents"
    __table_args__ = (
        Index("ix_source_task_documents_task_id", "source_task_id"),
        Index("ix_source_task_documents_run_id", "run_id"),
        Index("ix_source_task_documents_source_id", "source_id"),
        Index("ix_source_task_documents_content_hash", "content_hash"),
    )

    source_task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("crawled_documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    title: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    clean_text: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matched_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    source_task: Mapped[SourceTask] = relationship()
    run: Mapped[MonitoringRun] = relationship()
    source: Mapped[Source] = relationship()
    document: Mapped[CrawledDocument | None] = relationship()
