"""sync chat history across clients

Revision ID: 20260721_0002
Revises: 7ae01f5313bb
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260721_0002"
down_revision: str | None = "7ae01f5313bb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_logs", sa.Column("client_session_id", sa.String(length=128), nullable=True))
    op.add_column(
        "chat_logs",
        sa.Column("category_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.create_index("ix_chat_logs_client_session_id", "chat_logs", ["client_session_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_logs_client_session_id", table_name="chat_logs")
    op.drop_column("chat_logs", "category_ids")
    op.drop_column("chat_logs", "client_session_id")
