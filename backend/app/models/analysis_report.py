from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.monitoring_job import MonitoringJob
    from app.models.user import User


class AnalysisReport(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "analysis_reports"

    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("monitoring_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(1000), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    full_report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sources_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    pdf_path: Mapped[str | None] = mapped_column(String(2048))
    docx_path: Mapped[str | None] = mapped_column(String(2048))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    job: Mapped[MonitoringJob] = relationship(back_populates="report")
    creator: Mapped[User] = relationship(back_populates="reports")
