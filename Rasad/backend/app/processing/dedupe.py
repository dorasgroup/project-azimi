from difflib import SequenceMatcher

from app.processing.schemas import PreparedDocument
from app.processing.text import tokens


def _jaccard(left: str, right: str) -> float:
    left_tokens = set(tokens(left))
    right_tokens = set(tokens(right))
    if not left_tokens or not right_tokens:
        return 0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def is_duplicate(
    document: PreparedDocument,
    existing: PreparedDocument,
    *,
    title_threshold: float,
    content_threshold: float,
) -> bool:
    if (
        document.canonical_url == existing.canonical_url
        or document.content_hash == existing.content_hash
    ):
        return True
    title_similarity = SequenceMatcher(
        None, document.title.casefold(), existing.title.casefold()
    ).ratio()
    return (
        title_similarity >= title_threshold
        and _jaccard(
            document.clean_text,
            existing.clean_text,
        )
        >= content_threshold
    )
