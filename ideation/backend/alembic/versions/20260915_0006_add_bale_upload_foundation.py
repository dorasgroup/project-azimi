"""add Bale content-upload foundation

Revision ID: 20260915_0006
Revises: 20260915_0005
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260915_0006"
down_revision: str | None = "20260915_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bale_conversations",
        sa.Column("mode", sa.String(length=32), server_default="menu", nullable=False),
    )
    op.add_column(
        "bale_conversations",
        sa.Column("intent_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_check_constraint(
        "ck_bale_conversations_mode",
        "bale_conversations",
        "mode IN ('menu', 'select_repository', 'chat', 'upload')",
    )
    op.execute(
        "UPDATE bale_conversations SET mode = CASE "
        "WHEN selected_category_id IS NULL THEN 'menu' ELSE 'chat' END"
    )

    op.add_column("bale_updates", sa.Column("intent", sa.String(length=32), nullable=True))
    op.add_column(
        "bale_updates",
        sa.Column("category_id_snapshot", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "bale_updates", sa.Column("client_session_id_snapshot", sa.String(128), nullable=True)
    )
    op.add_column(
        "bale_updates", sa.Column("dify_conversation_id_snapshot", sa.String(128), nullable=True)
    )
    op.add_column("bale_updates", sa.Column("intent_version_snapshot", sa.Integer(), nullable=True))
    op.add_column("bale_updates", sa.Column("document_file_id", sa.Text(), nullable=True))
    op.add_column("bale_updates", sa.Column("document_file_unique_id", sa.Text(), nullable=True))
    op.add_column("bale_updates", sa.Column("document_filename", sa.String(500), nullable=True))
    op.add_column("bale_updates", sa.Column("document_mime_type", sa.String(255), nullable=True))
    op.add_column("bale_updates", sa.Column("document_size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("bale_updates", sa.Column("caption", sa.Text(), nullable=True))
    op.add_column(
        "bale_updates", sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "bale_updates",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bale_updates", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_bale_updates_category_snapshot",
        "bale_updates",
        "categories",
        ["category_id_snapshot"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_bale_updates_upload",
        "bale_updates",
        "uploads",
        ["upload_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_bale_updates_intent",
        "bale_updates",
        "intent IS NULL OR intent IN ('chat', 'upload', 'command', 'callback')",
    )
    op.create_index("ix_bale_updates_intent", "bale_updates", ["intent"])
    op.create_index("ix_bale_updates_upload_id", "bale_updates", ["upload_id"])

    op.add_column(
        "uploads",
        sa.Column("source", sa.String(32), server_default="legacy", nullable=False),
    )
    op.add_column("uploads", sa.Column("source_key", sa.String(255), nullable=True))
    op.add_column("uploads", sa.Column("classification_method", sa.String(64), nullable=True))
    op.add_column("uploads", sa.Column("classification_version", sa.String(64), nullable=True))
    op.add_column("uploads", sa.Column("classification_score", sa.Float(), nullable=True))
    op.add_column(
        "uploads",
        sa.Column(
            "classification_needs_review",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_uploads_source",
        "uploads",
        "source IN ('web', 'bale', 'trend', 'dify_sync', 'legacy')",
    )
    op.create_index("ix_uploads_source", "uploads", ["source"])
    op.create_index("uq_uploads_source_key", "uploads", ["source_key"], unique=True)
    op.execute(
        "UPDATE uploads SET source = 'trend' WHERE EXISTS "
        "(SELECT 1 FROM trend_imports WHERE trend_imports.upload_id = uploads.id)"
    )


def downgrade() -> None:
    op.drop_index("uq_uploads_source_key", table_name="uploads")
    op.drop_index("ix_uploads_source", table_name="uploads")
    op.drop_constraint("ck_uploads_source", "uploads", type_="check")
    op.drop_column("uploads", "classification_needs_review")
    op.drop_column("uploads", "classification_score")
    op.drop_column("uploads", "classification_version")
    op.drop_column("uploads", "classification_method")
    op.drop_column("uploads", "source_key")
    op.drop_column("uploads", "source")

    op.drop_index("ix_bale_updates_upload_id", table_name="bale_updates")
    op.drop_index("ix_bale_updates_intent", table_name="bale_updates")
    op.drop_constraint("ck_bale_updates_intent", "bale_updates", type_="check")
    op.drop_constraint("fk_bale_updates_upload", "bale_updates", type_="foreignkey")
    op.drop_constraint("fk_bale_updates_category_snapshot", "bale_updates", type_="foreignkey")
    op.drop_column("bale_updates", "lease_expires_at")
    op.drop_column("bale_updates", "processing_started_at")
    op.drop_column("bale_updates", "upload_id")
    op.drop_column("bale_updates", "caption")
    op.drop_column("bale_updates", "document_size_bytes")
    op.drop_column("bale_updates", "document_mime_type")
    op.drop_column("bale_updates", "document_filename")
    op.drop_column("bale_updates", "document_file_unique_id")
    op.drop_column("bale_updates", "document_file_id")
    op.drop_column("bale_updates", "intent_version_snapshot")
    op.drop_column("bale_updates", "dify_conversation_id_snapshot")
    op.drop_column("bale_updates", "client_session_id_snapshot")
    op.drop_column("bale_updates", "category_id_snapshot")
    op.drop_column("bale_updates", "intent")

    op.drop_constraint("ck_bale_conversations_mode", "bale_conversations", type_="check")
    op.drop_column("bale_conversations", "intent_version")
    op.drop_column("bale_conversations", "mode")
