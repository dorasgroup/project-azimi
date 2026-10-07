"""separate source connection and crawl status

Revision ID: f15a2d74c901
Revises: c7e31f4a92b0
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f15a2d74c901"
down_revision: str | None = "c7e31f4a92b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CRAWL_STATUSES = ("unknown", "success", "partial", "empty", "blocked", "failed")


def upgrade() -> None:
    crawl_status = postgresql.ENUM(*CRAWL_STATUSES, name="source_crawl_status")
    crawl_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "sources",
        sa.Column(
            "last_crawl_status",
            crawl_status,
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column("sources", sa.Column("last_crawl_message", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Recover useful crawl history where the old overloaded status fields make
    # the origin unambiguous. Unknown remains the safe default otherwise.
    op.execute(
        """
        UPDATE sources
        SET last_crawl_message = last_status_message,
            last_crawled_at = last_checked_at,
            last_crawl_status = CASE
                WHEN last_status_message LIKE '%خارج از بازه رصد%'
                  OR last_status_message LIKE '%هیچ URL خبری%'
                  OR last_status_message LIKE '%هیچ صفحه‌ای دریافت نشد%'
                    THEN 'empty'::source_crawl_status
                WHEN last_status_message LIKE '%تمام صفحه‌های انتخاب‌شده ناموفق%'
                    THEN 'failed'::source_crawl_status
                ELSE 'unknown'::source_crawl_status
            END
        WHERE last_status_message LIKE '%بازه رصد%'
           OR last_status_message LIKE '%URL خبری%'
           OR last_status_message LIKE '%صفحه‌های انتخاب‌شده%'
           OR last_status_message LIKE '%هیچ صفحه‌ای دریافت نشد%'
        """
    )
    op.execute(
        """
        UPDATE sources AS source
        SET last_crawl_status = 'success',
            last_crawled_at = COALESCE(source.last_crawled_at, source.last_checked_at)
        WHERE EXISTS (
            SELECT 1
            FROM crawled_documents AS document
            WHERE document.source_id = source.id
        )
        """
    )

    # Preserve historical reachability: a valid/restricted HTTP response means
    # the source endpoint was reachable even when a topic/date crawl yielded zero pages.
    op.execute(
        """
        UPDATE sources
        SET last_status = 'success'
        WHERE last_http_status BETWEEN 200 AND 399
           OR last_http_status IN (401, 403, 429)
        """
    )


def downgrade() -> None:
    op.drop_column("sources", "last_crawled_at")
    op.drop_column("sources", "last_crawl_message")
    op.drop_column("sources", "last_crawl_status")
    postgresql.ENUM(name="source_crawl_status").drop(op.get_bind(), checkfirst=True)
