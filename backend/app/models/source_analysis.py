from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.modules.analysis.domain.source_analysis import SourceAnalysisStatus

if TYPE_CHECKING:
    from app.models.monitoring_run import MonitoringRun
    from app.models.source import Source
    from app.models.source_task import SourceTask


class OrmRunAnalysisSpec(UUIDPrimaryKeyMixin, Base):
    """Durable snapshot of resolved analysis configuration for a monitoring run."""

    __tablename__ = "run_analysis_specs"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_analysis_specs_run_id"),
        Index("ix_run_analysis_specs_run_id", "run_id"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    spec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    run: Mapped[MonitoringRun] = relationship()


class OrmSourceAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "source_analyses"
    __table_args__ = (
        UniqueConstraint("source_task_id", name="uq_source_analyses_source_task_id"),
        Index("ix_source_analyses_status", "status"),
        Index("ix_source_analyses_run_id", "run_id"),
        Index("ix_source_analyses_source_id", "source_id"),
    )

    source_task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_tasks.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
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
        index=True,
    )
    status: Mapped[SourceAnalysisStatus] = mapped_column(
        Enum(
            SourceAnalysisStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
        default=SourceAnalysisStatus.PENDING,
        server_default=SourceAnalysisStatus.PENDING.value,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    analysis_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    source_task: Mapped[SourceTask] = relationship()
    run: Mapped[MonitoringRun] = relationship()
    source: Mapped[Source] = relationship()
    result: Mapped[OrmSourceAnalysisResult | None] = relationship(back_populates="source_analysis", uselist=False)


class OrmSourceAnalysisResult(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "source_analysis_results"
    __table_args__ = (
        Index("ix_source_analysis_results_run_id", "run_id"),
        Index("ix_source_analysis_results_task_id", "source_task_id"),
    )

    source_analysis_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_analyses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
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
        index=True,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="1.0", server_default="1.0")
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    findings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    evidence_map: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    raw_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    source_analysis: Mapped[OrmSourceAnalysis] = relationship(back_populates="result")
    source_task: Mapped[SourceTask] = relationship()
    run: Mapped[MonitoringRun] = relationship()
    source: Mapped[Source] = relationship()
