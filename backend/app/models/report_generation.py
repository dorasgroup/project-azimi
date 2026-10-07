"""Durable reporting lifecycle and per-format artifact metadata."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class OrmReportGeneration(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "report_generations"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "aggregated_analysis_result_id",
            name="uq_report_generations_run_aggregate",
        ),
        Index("ix_report_generations_status_lease", "status", "lease_expires_at"),
        CheckConstraint("status IN ('pending','running','completed','failed','cancelled')", name="valid_status"),
        CheckConstraint("status <> 'running' OR (claim_token IS NOT NULL AND claimed_at IS NOT NULL AND lease_expires_at IS NOT NULL)", name="running_lease"),
        CheckConstraint("attempt_count >= 0", name="nonnegative_attempts"),
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("monitoring_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    aggregated_analysis_result_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("aggregated_analysis_results.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    spec_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    spec_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    sanitized_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )

    artifacts: Mapped[list["OrmReportArtifact"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan",
    )


class OrmReportArtifact(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "report_artifacts"
    __table_args__ = (
        UniqueConstraint("report_generation_id", "format", name="uq_report_artifacts_generation_format"),
        CheckConstraint("format IN ('pdf','docx')", name="valid_format"),
        CheckConstraint("content_size > 0", name="positive_size"),
        CheckConstraint("content_checksum ~ '^[0-9a-f]{64}$'", name="valid_checksum"),
    )

    report_generation_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("report_generations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    content_size: Mapped[int] = mapped_column(Integer, nullable=False)
    report_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    template_version: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    generation: Mapped[OrmReportGeneration] = relationship(back_populates="artifacts")
