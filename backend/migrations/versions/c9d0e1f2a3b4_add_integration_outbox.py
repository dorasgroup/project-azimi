"""add durable integration outbox

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "integration_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_key", sa.String(200), nullable=False),
        sa.Column("message_type", sa.String(64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True)),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("consumer_acked_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error_code", sa.String(100)),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("message_key", name="uq_integration_outbox_message_key"),
    )
    op.create_index("ix_integration_outbox_status_available", "integration_outbox", ["status", "available_at"])
    op.create_index("ix_integration_outbox_status_next_attempt", "integration_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_integration_outbox_lease_expires", "integration_outbox", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_integration_outbox_lease_expires", table_name="integration_outbox")
    op.drop_index("ix_integration_outbox_status_next_attempt", table_name="integration_outbox")
    op.drop_index("ix_integration_outbox_status_available", table_name="integration_outbox")
    op.drop_table("integration_outbox")
