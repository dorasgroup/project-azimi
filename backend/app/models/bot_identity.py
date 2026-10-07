from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.monitoring_job import MonitoringJob
    from app.models.user import User


class BotIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "bot_identities"
    __table_args__ = (
        Index(
            "uq_bot_identities_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default = true"),
        ),
    )

    title: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    role_definition: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[str | None] = mapped_column(String(250))
    analysis_style: Mapped[str | None] = mapped_column(Text)
    cognitive_framework: Mapped[str | None] = mapped_column(Text)
    output_structure: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    source_policy: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )

    creator: Mapped[User] = relationship(back_populates="bot_identities")
    monitoring_jobs: Mapped[list[MonitoringJob]] = relationship(back_populates="bot_identity")
