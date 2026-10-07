from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import LogLevel


class SystemLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "system_logs"

    level: Mapped[LogLevel] = mapped_column(
        Enum(
            LogLevel, name="log_level", values_callable=lambda enum: [item.value for item in enum]
        ),
        nullable=False,
        index=True,
    )
    module: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    request_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    job_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
