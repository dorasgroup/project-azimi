from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, Enum, ForeignKey, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import JobStatus

if TYPE_CHECKING:
    from app.models.analysis_report import AnalysisReport
    from app.models.bot_identity import BotIdentity
    from app.models.crawled_document import CrawledDocument
    from app.models.monitoring_run import MonitoringRun
    from app.models.source import Source
    from app.models.user import User

monitoring_job_sources = Table(
    "monitoring_job_sources",
    Base.metadata,
    Column(
        "job_id",
        PGUUID(as_uuid=True),
        ForeignKey("monitoring_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "source_id",
        PGUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
)


class MonitoringJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "monitoring_jobs"
    __table_args__ = (
        CheckConstraint("date_from_gregorian <= date_to_gregorian", name="valid_date_range"),
    )

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_from_jalali: Mapped[str] = mapped_column(String(10), nullable=False)
    date_to_jalali: Mapped[str] = mapped_column(String(10), nullable=False)
    date_from_gregorian: Mapped[date] = mapped_column(nullable=False, index=True)
    date_to_gregorian: Mapped[date] = mapped_column(nullable=False, index=True)
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus, name="job_status", values_callable=lambda enum: [item.value for item in enum]
        ),
        nullable=False,
        default=JobStatus.PENDING,
        server_default=JobStatus.PENDING.value,
        index=True,
    )
    requested_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    bot_identity_id: Mapped[UUID] = mapped_column(
        ForeignKey("bot_identities.id", ondelete="RESTRICT"),
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)

    requester: Mapped[User] = relationship(back_populates="monitoring_jobs")
    bot_identity: Mapped[BotIdentity] = relationship(back_populates="monitoring_jobs")
    sources: Mapped[list[Source]] = relationship(
        secondary=monitoring_job_sources,
        back_populates="monitoring_jobs",
    )
    documents: Mapped[list[CrawledDocument]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
    )
    report: Mapped[AnalysisReport | None] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        uselist=False,
    )
    runs: Mapped[list[MonitoringRun]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="MonitoringRun.created_at.desc()",
    )
