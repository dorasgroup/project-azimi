"""Allow public Haminyar article provenance in the deployed upload constraint."""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20261012_0011"
down_revision: str | None = "20261011_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_uploads_source", "uploads", type_="check")
    op.create_check_constraint(
        "ck_uploads_source",
        "uploads",
        "source IN ('web', 'bale', 'trend', 'dify_sync', 'legacy', 'haminyar_article')",
    )


def downgrade() -> None:
    # Never erase provenance or silently recategorize data to make a rollback fit.
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM uploads WHERE source = 'haminyar_article'")
    ):
        raise RuntimeError("Cannot downgrade while Haminyar article uploads exist")
    op.drop_constraint("ck_uploads_source", "uploads", type_="check")
    op.create_check_constraint(
        "ck_uploads_source",
        "uploads",
        "source IN ('web', 'bale', 'trend', 'dify_sync', 'legacy')",
    )
