from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.monitoring.domain.execution import MonitoringRunStatus

if TYPE_CHECKING:
    from app.models.monitoring_job import MonitoringJob
    from app.models.source_task import SourceTask


class MonitoringRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monitoring_runs"
    __table_args__ = (
        Index("ix_monitoring_runs_job_id", "monitoring_job_id"),
        Index(
            "uq_monitoring_runs_active_job",
            "monitoring_job_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    monitoring_job_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[MonitoringRunStatus] = mapped_column(
        Enum(
            MonitoringRunStatus,
            name="monitoring_run_status",
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=MonitoringRunStatus.PENDING,
        server_default=MonitoringRunStatus.PENDING.value,
        index=True,
    )
    execution_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="legacy",
        server_default="legacy",
    )
    expected_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    terminal_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    successful_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    failed_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    cancelled_sources: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[MonitoringJob] = relationship(back_populates="runs")
    tasks: Mapped[list[SourceTask]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="SourceTask.ordinal",
    )


from sqlalchemy import event, inspect
from app.shared.domain.errors import ConflictError


@event.listens_for(MonitoringRun, "before_update")
def _enforce_monitoring_run_execution_mode_immutable(mapper, connection, target: MonitoringRun) -> None:
    state = inspect(target)
    history = state.get_history("execution_mode", True)
    if history.has_changes():
        old_val = history.deleted[0] if history.deleted else None
        if old_val != target.execution_mode:
            raise ConflictError(
                f"execution_mode of MonitoringRun {target.id} is immutable and cannot be changed from '{old_val}' to '{target.execution_mode}'."
            )
