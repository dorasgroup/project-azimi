from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole

if TYPE_CHECKING:
    from app.models.analysis_report import AnalysisReport
    from app.models.bot_identity import BotIdentity
    from app.models.monitoring_job import MonitoringJob
    from app.models.refresh_token import RefreshToken
    from app.models.source import Source


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole, name="user_role", values_callable=lambda enum: [item.value for item in enum]
        ),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    sources: Mapped[list[Source]] = relationship(back_populates="creator")
    bot_identities: Mapped[list[BotIdentity]] = relationship(back_populates="creator")
    monitoring_jobs: Mapped[list[MonitoringJob]] = relationship(back_populates="requester")
    reports: Mapped[list[AnalysisReport]] = relationship(back_populates="creator")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
