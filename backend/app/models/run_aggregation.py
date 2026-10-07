"""ORM models for run-level aggregation and aggregated results."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
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

from app.db.base import Base, UUIDPrimaryKeyMixin


class OrmRunAggregation(UUIDPrimaryKeyMixin, Base):
    """Database model for tracking run-level analysis aggregation execution."""

    __tablename__ = "run_aggregations"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_aggregations_run_id"),
        Index("ix_run_aggregations_run_id", "run_id", unique=True),
        Index("ix_run_aggregations_status", "status"),
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
        default="pending",
        server_default="pending",
    )
    readiness_reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    manifest_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    run_analysis_spec_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
    )
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    claim_token: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    inputs: Mapped[list["OrmRunAggregationInput"]] = relationship(
        back_populates="aggregation",
        cascade="all, delete-orphan",
        order_by="OrmRunAggregationInput.position",
    )
    result: Mapped["OrmAggregatedAnalysisResult | None"] = relationship(
        back_populates="aggregation",
        uselist=False,
        cascade="all, delete-orphan",
    )


class OrmRunAggregationInput(UUIDPrimaryKeyMixin, Base):
    """Immutable input manifest entry linking a RunAggregation to a SourceAnalysisResult."""

    __tablename__ = "run_aggregation_inputs"
    __table_args__ = (
        UniqueConstraint(
            "aggregation_id",
            "source_analysis_result_id",
            name="uq_run_aggregation_inputs_agg_result",
        ),
        Index("ix_run_aggregation_inputs_aggregation_id", "aggregation_id"),
    )

    aggregation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run_aggregations.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_analysis_result_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("source_analysis_results.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    input_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    schema_version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="1.0",
        server_default="1.0",
    )

    aggregation: Mapped[OrmRunAggregation] = relationship(back_populates="inputs")


class OrmAggregatedAnalysisResult(UUIDPrimaryKeyMixin, Base):
    """Durable, provenance-backed result of run-level analysis aggregation."""

    __tablename__ = "aggregated_analysis_results"
    __table_args__ = (
        UniqueConstraint(
            "aggregation_id",
            name="uq_aggregated_analysis_results_aggregation_id",
        ),
        Index("ix_aggregated_analysis_results_aggregation_id", "aggregation_id", unique=True),
        Index("ix_aggregated_analysis_results_run_id", "run_id"),
    )

    aggregation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("run_aggregations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    schema_version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="1.0",
        server_default="1.0",
    )
    manifest_digest: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    manifest_schema_version: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="2.0",
        server_default="2.0",
    )
    readiness_reason: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    cutoff_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expected_sources: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    successful_sources: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    failed_sources: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    cancelled_sources: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    analysis_successful: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    analysis_failed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    analysis_cancelled: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    findings: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    coverage_metadata: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    provenance_references: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        server_default="[]",
    )
    raw_result: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    aggregation: Mapped[OrmRunAggregation] = relationship(back_populates="result")
