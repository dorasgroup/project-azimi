"""ORM models for run completion tracking and deduplication."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OrmRunCompletionTracker(UUIDPrimaryKeyMixin, Base):
    """Database model for tracking run-level analysis terminal states and readiness."""

    __tablename__ = "run_completion_trackers"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_completion_trackers_run_id"),
        Index("ix_run_completion_trackers_run_id", "run_id", unique=True),
        Index("ix_run_completion_trackers_status", "status"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="open",
        server_default="open",
    )
    analysis_terminal_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_successful_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_failed_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    analysis_cancelled_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    readiness_reason: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    aggregation_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    analysis_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    deadline_scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class OrmRunCompletionEvent(UUIDPrimaryKeyMixin, Base):
    """Durable deduplication store for terminal completion events."""

    __tablename__ = "run_completion_events"
    __table_args__ = (
        UniqueConstraint("event_kind", "entity_id", name="uq_run_completion_events_kind_entity"),
        Index("ix_run_completion_events_run_id", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_kind: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    entity_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
