"""add inactive Bale bot integration state

Revision ID: 20260915_0005
Revises: 20260914_0004
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260915_0005"
down_revision: str | None = "20260914_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "categories",
        sa.Column(
            "available_in_bale",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_categories_available_in_bale",
        "categories",
        ["available_in_bale"],
    )
    op.create_table(
        "bale_conversations",
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("bale_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_type", sa.String(length=32), nullable=False),
        sa.Column("selected_category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("client_session_id", sa.String(length=128), nullable=False),
        sa.Column("dify_conversation_id", sa.String(length=128), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["selected_category_id"],
            ["categories.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chat_id"),
    )
    op.create_index("ix_bale_conversations_chat_id", "bale_conversations", ["chat_id"])
    op.create_index(
        "ix_bale_conversations_bale_user_id",
        "bale_conversations",
        ["bale_user_id"],
    )
    op.create_index(
        "ix_bale_conversations_selected_category_id",
        "bale_conversations",
        ["selected_category_id"],
    )
    op.create_table(
        "bale_updates",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("bale_user_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("callback_query_id", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("callback_data", sa.String(length=64), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("update_id"),
    )
    op.create_index("ix_bale_updates_kind", "bale_updates", ["kind"])
    op.create_index("ix_bale_updates_status", "bale_updates", ["status"])
    op.create_index("ix_bale_updates_chat_id", "bale_updates", ["chat_id"])


def downgrade() -> None:
    op.drop_index("ix_bale_updates_chat_id", table_name="bale_updates")
    op.drop_index("ix_bale_updates_status", table_name="bale_updates")
    op.drop_index("ix_bale_updates_kind", table_name="bale_updates")
    op.drop_table("bale_updates")
    op.drop_index("ix_bale_conversations_selected_category_id", table_name="bale_conversations")
    op.drop_index("ix_bale_conversations_bale_user_id", table_name="bale_conversations")
    op.drop_index("ix_bale_conversations_chat_id", table_name="bale_conversations")
    op.drop_table("bale_conversations")
    op.drop_index("ix_categories_available_in_bale", table_name="categories")
    op.drop_column("categories", "available_in_bale")
