from collections.abc import Sequence
from uuid import UUID

from app.models.category import Category

CATEGORY_PAGE_SIZE = 8
MENU_UPLOAD_CALLBACK = "menu:upload"
MENU_REPOSITORIES_CALLBACK = "menu:repositories"


def main_menu_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [
                {"text": "بارگذاری محتوا", "callback_data": MENU_UPLOAD_CALLBACK},
                {"text": "انتخاب مخزن", "callback_data": MENU_REPOSITORIES_CALLBACK},
            ],
        ]
    }


def category_callback(category_id: UUID) -> str:
    value = f"cat:{category_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Bale callback_data exceeds 64 bytes")
    return value


def categories_page_callback(page: int) -> str:
    return f"cats:{max(0, page)}"


def upload_category_callback(update_id: int, category_id: UUID) -> str:
    value = f"upcat:{update_id}:{category_id}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Bale callback_data exceeds 64 bytes")
    return value


def upload_categories_page_callback(update_id: int, page: int) -> str:
    value = f"upcats:{update_id}:{max(0, page)}"
    if len(value.encode("utf-8")) > 64:
        raise ValueError("Bale callback_data exceeds 64 bytes")
    return value


def parse_category_callback(value: str | None) -> UUID | None:
    if not value or not value.startswith("cat:"):
        return None
    try:
        return UUID(value.removeprefix("cat:"))
    except ValueError:
        return None


def parse_categories_page_callback(value: str | None) -> int | None:
    if not value or not value.startswith("cats:"):
        return None
    try:
        return max(0, int(value.removeprefix("cats:")))
    except ValueError:
        return None


def parse_upload_category_callback(value: str | None) -> tuple[int, UUID] | None:
    if not value or not value.startswith("upcat:"):
        return None
    parts = value.split(":", 2)
    try:
        return int(parts[1]), UUID(parts[2])
    except (IndexError, ValueError):
        return None


def parse_upload_categories_page_callback(value: str | None) -> tuple[int, int] | None:
    if not value or not value.startswith("upcats:"):
        return None
    parts = value.split(":", 2)
    try:
        return int(parts[1]), max(0, int(parts[2]))
    except (IndexError, ValueError):
        return None


def category_keyboard(
    categories: Sequence[Category],
    *,
    page: int = 0,
) -> dict[str, list[list[dict[str, str]]]]:
    page_count = max(1, (len(categories) + CATEGORY_PAGE_SIZE - 1) // CATEGORY_PAGE_SIZE)
    safe_page = min(max(0, page), page_count - 1)
    start = safe_page * CATEGORY_PAGE_SIZE
    rows: list[list[dict[str, str]]] = [
        [
            {
                "text": category.name[:80],
                "callback_data": category_callback(category.id),
            }
        ]
        for category in categories[start : start + CATEGORY_PAGE_SIZE]
    ]
    navigation: list[dict[str, str]] = []
    if safe_page > 0:
        navigation.append(
            {"text": "صفحه قبل", "callback_data": categories_page_callback(safe_page - 1)}
        )
    if safe_page + 1 < page_count:
        navigation.append(
            {"text": "صفحه بعد", "callback_data": categories_page_callback(safe_page + 1)}
        )
    if navigation:
        rows.append(navigation)
    return {"inline_keyboard": rows}


def upload_category_keyboard(
    categories: Sequence[Category],
    *,
    update_id: int,
    page: int = 0,
) -> dict[str, list[list[dict[str, str]]]]:
    page_count = max(1, (len(categories) + CATEGORY_PAGE_SIZE - 1) // CATEGORY_PAGE_SIZE)
    safe_page = min(max(0, page), page_count - 1)
    start = safe_page * CATEGORY_PAGE_SIZE
    rows: list[list[dict[str, str]]] = [
        [
            {
                "text": category.name[:80],
                "callback_data": upload_category_callback(update_id, category.id),
            }
        ]
        for category in categories[start : start + CATEGORY_PAGE_SIZE]
    ]
    navigation: list[dict[str, str]] = []
    if safe_page > 0:
        navigation.append(
            {
                "text": "صفحه قبل",
                "callback_data": upload_categories_page_callback(update_id, safe_page - 1),
            }
        )
    if safe_page + 1 < page_count:
        navigation.append(
            {
                "text": "صفحه بعد",
                "callback_data": upload_categories_page_callback(update_id, safe_page + 1),
            }
        )
    if navigation:
        rows.append(navigation)
    return {"inline_keyboard": rows}


def change_category_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    return {
        "inline_keyboard": [
            [{"text": "تغییر مخزن", "callback_data": categories_page_callback(0)}],
            [{"text": "گفت‌وگوی جدید", "callback_data": "new:chat"}],
            [{"text": "منوی اصلی", "callback_data": "menu:home"}],
        ]
    }
