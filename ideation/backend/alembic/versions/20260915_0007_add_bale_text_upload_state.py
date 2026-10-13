"""add Bale text-upload processing state

Revision ID: 20260915_0007
Revises: 20260915_0006
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260915_0007"
down_revision: str | None = "20260915_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_bale_conversations_mode", "bale_conversations", type_="check")
    op.create_check_constraint(
        "ck_bale_conversations_mode",
        "bale_conversations",
        "mode IN ('menu', 'select_repository', 'chat', 'upload', "
        "'upload_processing', 'upload_category')",
    )
    op.add_column(
        "bale_conversations",
        sa.Column("pending_upload_update_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_bale_conversations_pending_upload",
        "bale_conversations",
        "bale_updates",
        ["pending_upload_update_id"],
        ["update_id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "bale_updates", sa.Column("classification_method", sa.String(64), nullable=True)
    )
    op.add_column("bale_updates", sa.Column("classification_score", sa.Float(), nullable=True))
    op.add_column(
        "bale_updates",
        sa.Column(
            "classification_needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("bale_updates", "classification_needs_review")
    op.drop_column("bale_updates", "classification_score")
    op.drop_column("bale_updates", "classification_method")
    op.drop_constraint(
        "fk_bale_conversations_pending_upload",
        "bale_conversations",
        type_="foreignkey",
    )
    op.drop_column("bale_conversations", "pending_upload_update_id")
    op.drop_constraint("ck_bale_conversations_mode", "bale_conversations", type_="check")
    op.create_check_constraint(
        "ck_bale_conversations_mode",
        "bale_conversations",
        "mode IN ('menu', 'select_repository', 'chat', 'upload')",
    )
