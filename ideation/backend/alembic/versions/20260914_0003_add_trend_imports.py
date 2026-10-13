"""add trend imports

Revision ID: 20260914_0003
Revises: 20260721_0002
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260914_0003"
down_revision: str | None = "20260721_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trend_imports",
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_trend_id", sa.String(length=200), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=50), nullable=False),
        sa.Column("dify_status", sa.String(length=50), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["upload_id"], ["uploads.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_system",
            "source_trend_id",
            "source_version",
            name="uq_trend_imports_source_trend",
        ),
    )
    op.create_index(
        "ix_trend_imports_delivery_status",
        "trend_imports",
        ["delivery_status"],
    )
    op.create_index("ix_trend_imports_dify_status", "trend_imports", ["dify_status"])
    op.create_index(
        "ix_trend_imports_idempotency_key",
        "trend_imports",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_trend_imports_source_system",
        "trend_imports",
        ["source_system"],
    )
    op.create_index("ix_trend_imports_upload_id", "trend_imports", ["upload_id"])


def downgrade() -> None:
    op.drop_index("ix_trend_imports_upload_id", table_name="trend_imports")
    op.drop_index("ix_trend_imports_source_system", table_name="trend_imports")
    op.drop_index("ix_trend_imports_idempotency_key", table_name="trend_imports")
    op.drop_index("ix_trend_imports_dify_status", table_name="trend_imports")
    op.drop_index("ix_trend_imports_delivery_status", table_name="trend_imports")
    op.drop_table("trend_imports")
