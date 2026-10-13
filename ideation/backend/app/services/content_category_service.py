from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.services.trend_category_service import (
    category_relevance_score,
    normalize_category_text,
)

MIN_CONFIDENT_SCORE = 6


@dataclass(frozen=True)
class ContentCategoryMatch:
    category_id: UUID | None
    category_name: str | None
    score: int
    runner_up_score: int

    @property
    def is_confident(self) -> bool:
        return (
            self.category_id is not None
            and self.score >= MIN_CONFIDENT_SCORE
            and self.score > self.runner_up_score
        )


def rank_content_category(
    categories: list[Category],
    content: str,
) -> ContentCategoryMatch:
    body = normalize_category_text(content)
    ranked = sorted(
        (
            (
                category_relevance_score(category.name, topic="", body=body),
                normalize_category_text(category.name),
                category,
            )
            for category in categories
        ),
        key=lambda item: (-item[0], item[1]),
    )
    if not ranked:
        return ContentCategoryMatch(None, None, 0, 0)
    score, _, category = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if score < MIN_CONFIDENT_SCORE or score == runner_up:
        return ContentCategoryMatch(None, None, score, runner_up)
    return ContentCategoryMatch(category.id, category.name, score, runner_up)


async def classify_content_category(
    session: AsyncSession,
    content: str,
) -> ContentCategoryMatch:
    categories = list(
        (await session.execute(select(Category).order_by(Category.name))).scalars().all()
    )
    return rank_content_category(categories, content)
