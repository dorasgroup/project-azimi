from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.monitoring.domain.execution import SourceTaskStatus

if TYPE_CHECKING:
    from app.models.monitoring_run import MonitoringRun
    from app.models.source import Source


class SourceTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "source_id", name="uq_source_tasks_run_id_source_id"),
        Index("ix_source_tasks_status_lease_expires", "status", "lease_expires_at"),
        Index("ix_source_tasks_run_id_status", "run_id", "status"),
        CheckConstraint(
            "status != 'running' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_source_tasks_running_lease_invariants",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[SourceTaskStatus] = mapped_column(
        Enum(
            SourceTaskStatus,
            name="source_task_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceTaskStatus.PENDING,
        server_default=SourceTaskStatus.PENDING.value,
    )
    execution_spec: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    documents_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[MonitoringRun] = relationship(back_populates="tasks")
    source: Mapped[Source] = relationship()
