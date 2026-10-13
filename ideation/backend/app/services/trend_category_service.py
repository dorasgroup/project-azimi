import html
import re
import unicodedata
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.category import Category
from app.models.upload import upload_categories
from app.schemas.trend_integration import TrendImportRequest

_CHAR_TRANSLATION = str.maketrans(
    {
        "ي": "ی",
        "ى": "ی",
        "ك": "ک",
        "ة": "ه",
        "ۀ": "ه",
        "ؤ": "و",
        "إ": "ا",
        "أ": "ا",
        "ٱ": "ا",
        "\u200c": " ",
    }
)
_TOKEN_RE = re.compile(r"[\u0600-\u06ff0-9]+")
_SPACE_RE = re.compile(r"\s+")
_STOP_WORDS = {
    "برای",
    "درباره",
    "گروه",
    "های",
    "این",
    "یک",
    "در",
    "به",
    "از",
    "و",
}
_CATEGORY_ALIASES = {
    "ازدواج": {
        "عقد",
        "عروسی",
        "نامزدی",
        "زوج",
        "زوجین",
        "همسر",
        "حلقه ازدواج",
    },
    "کنشگری زنان": {
        "حقوق زنان",
        "فعالیت زنان",
        "فعالیت اجتماعی زنان",
        "بانوان",
        "دختران",
        "کنشگر زن",
    },
    "گروه تولید": {
        "تولید",
        "تولیدکننده",
        "تولیدکنندگان",
        "کارخانه",
        "صنعت",
        "صنعتی",
        "کارآفرینی",
    },
}


def normalize_category_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", html.unescape(value or ""))
    normalized = normalized.translate(_CHAR_TRANSLATION)
    return _SPACE_RE.sub(" ", normalized).strip().lower()


def _contains(text: str, tokens: set[str], term: str) -> bool:
    normalized_term = normalize_category_text(term)
    if " " in normalized_term:
        return normalized_term in text
    return normalized_term in tokens


def _payload_text(payload: TrendImportRequest) -> tuple[str, str]:
    topic = normalize_category_text(payload.topic)
    body = normalize_category_text(
        " ".join(
            [payload.title, payload.summary, *(item.text for item in payload.items)]
        )
    )
    return topic, body


def category_relevance_score(
    category_name: str,
    *,
    topic: str,
    body: str,
) -> int:
    name = normalize_category_text(category_name)
    if not name:
        return 0
    topic_tokens = set(_TOKEN_RE.findall(topic))
    body_tokens = set(_TOKEN_RE.findall(body))
    name_tokens = {
        token for token in _TOKEN_RE.findall(name) if token not in _STOP_WORDS
    }

    score = 0
    if topic == name:
        score += 40
    elif _contains(topic, topic_tokens, name):
        score += 24
    if _contains(body, body_tokens, name):
        score += 18
    score += 10 * len(name_tokens & topic_tokens)
    score += 5 * len(name_tokens & body_tokens)

    aliases = _CATEGORY_ALIASES.get(name, set())
    for alias in aliases:
        normalized_alias = normalize_category_text(alias)
        if normalized_alias == name or normalized_alias in name_tokens:
            continue
        if _contains(topic, topic_tokens, normalized_alias):
            score += 9
        if _contains(body, body_tokens, normalized_alias):
            score += 6
    return score


def choose_trend_category(
    categories: Iterable[Category],
    payload: TrendImportRequest,
) -> Category | None:
    topic, body = _payload_text(payload)
    ranked = sorted(
        (
            (category_relevance_score(category.name, topic=topic, body=body), category)
            for category in categories
        ),
        key=lambda item: (-item[0], normalize_category_text(item[1].name)),
    )
    if not ranked or ranked[0][0] < 6:
        return None
    if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
        return None
    return ranked[0][1]


async def classify_trend_category(
    session: AsyncSession,
    payload: TrendImportRequest,
) -> Category | None:
    categories = list(
        (
            await session.execute(select(Category).order_by(Category.name))
        ).scalars().all()
    )
    return choose_trend_category(categories, payload)


async def assign_category_if_empty(
    session: AsyncSession,
    *,
    upload_id: UUID,
    payload: TrendImportRequest,
) -> Category | None:
    assigned_count = await session.scalar(
        select(func.count())
        .select_from(upload_categories)
        .where(upload_categories.c.upload_id == upload_id)
    )
    if assigned_count:
        return None
    category = await classify_trend_category(session, payload)
    if category is None:
        return None
    await session.execute(
        insert(upload_categories)
        .values(upload_id=upload_id, category_id=category.id)
        .on_conflict_do_nothing()
    )
    await session.commit()
    return category
