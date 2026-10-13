"""Add independent Haminyar article provenance and synchronization state."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20261011_0010"
down_revision: str | None = "20260919_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "site_articles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("modified_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_modified_at", sa.DateTime(timezone=True)),
        sa.Column("source_status", sa.String(32), nullable=False, server_default="published"),
        sa.Column("content_sha256", sa.String(64)),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
        *[
            sa.Column(
                column,
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("uploads.id", ondelete="RESTRICT"),
            )
            for column in ("active_upload_id", "pending_upload_id", "retired_upload_id")
        ],
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("dify_completed_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_site_articles_external_id", "site_articles", ["external_id"], unique=True)
    for column in ("active_upload_id", "pending_upload_id", "source_status"):
        op.create_index(f"ix_site_articles_{column}", "site_articles", [column])
    op.create_table(
        "site_article_sync_state",
        sa.Column("key", sa.String(32), primary_key=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("source_article_count", sa.Integer()),
        sa.Column("error_message", sa.Text()),
    )


def downgrade() -> None:
    op.drop_table("site_article_sync_state")
    op.drop_table("site_articles")
