"""Resolve inline references only against current, authorized article chunks.

The model chooses reference positions/attribution, never the URL. Missing or
unknown references are omitted, not guessed from the retrieval candidate list.
"""

import re
from secrets import token_hex
from urllib.parse import urlsplit

from app.schemas.chat import ChatSource

MARKER = re.compile(r"\[\[source:([A-Za-z0-9_-]{1,64})\]\]")
RESERVED_MARKER = re.compile(r"\[\[source:[^\]\n]*(?:\]\]|$)", re.IGNORECASE)


def safe_article_url(value: str | None) -> str | None:
    if not value or any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        url = urlsplit(value)
        if (
            url.scheme == "https"
            and url.hostname in {"haminyar.ir", "www.haminyar.ir"}
            and not url.username
            and not url.password
            and url.port in {None, 443}
            and url.path.startswith("/")
        ):
            return value
    except ValueError:
        pass
    return None


def without_citation_markers(answer: str) -> str:
    return RESERVED_MARKER.sub("", answer)


def build_chat_sources(
    chunks: list[dict],
    names: dict[str, str],
    articles: dict[str, tuple[str, str]],
    *,
    enabled: bool,
) -> list[ChatSource]:
    article_tokens: dict[str, str] = {}
    sources: list[ChatSource] = []
    for chunk in chunks:
        document_id = str(chunk.get("document_id") or "")
        source = ChatSource(
            document_name=names.get(document_id, chunk.get("source") or "نامشخص"),
            content=chunk.get("text"),
            score=float((chunk.get("_additional") or {}).get("score") or 0),
            document_id=document_id or None,
        )
        article = articles.get(document_id) if enabled else None
        url = safe_article_url(article[1]) if article else None
        if article and url:
            if document_id not in article_tokens:
                article_tokens[document_id] = f"h{len(article_tokens) + 1}_{token_hex(6)}"
            token = article_tokens[document_id]
            source = source.model_copy(update={
                "citation_id": token,
                "source_type": "haminyar_article",
                "source_url": url,
                "article_title": article[0],
            })
        sources.append(source)
    return sources


def citation_prompt(sources: list[ChatSource]) -> str:
    if not any(source.citation_id for source in sources):
        return ""
    return (
        "قواعد ارجاع قابل نمایش:\n"
        "- فقط برای بند یا خطی که واقعاً از مفهوم یا اطلاعات یک مقاله دارای برچسب "
        "ارجاع استفاده می‌کند، همان برچسب را در پایان همان بند یا خط بنویس.\n"
        "- برچسب‌ها را دقیقاً به شکل [[source:شناسه]] و فقط با شناسه ارائه‌شده بنویس؛ "
        "این برچسب ماشینی است و استثنای ممنوعیت اشاره به مراجع داخلی است.\n"
        "- برای بند دارای چند پشتوانه، برچسب‌های همان مقاله‌ها را کنار هم بگذار. "
        "برای بند بدون پشتوانه مقاله، هیچ برچسبی ننویس.\n"
        "- ایده تازه می‌تواند از مفهوم مقاله الهام بگیرد؛ ارجاع به پشتوانه مفهومی است، "
        "نه ادعای اینکه متن ایده عیناً در مقاله نوشته شده است.\n"
        "- برای فایل، ترند، متن بله یا مرجع بدون برچسب، ارجاع نساز. "
        "هیچ لینک یا شناسه دیگری تولید نکن و فهرست منابع انتهایی نساز.\n"
    )


def finalize_citations(answer: str, sources: list[ChatSource]) -> tuple[str, list[ChatSource]]:
    # Reasoning tokens are not answer evidence and must never produce citations.
    answer = re.sub(r"<think>[\s\S]*?(?:</think>\s*|$)", "", answer, flags=re.IGNORECASE)
    eligible = {
        source.citation_id
        for source in sources
        if source.source_type == "haminyar_article"
        and safe_article_url(source.source_url)
        and source.citation_id
    }
    used: set[str] = set()

    def validate(match: re.Match) -> str:
        valid = MARKER.fullmatch(match.group())
        if valid and valid[1] in eligible:
            used.add(valid[1])
            return match.group()
        return ""

    answer = RESERVED_MARKER.sub(validate, answer)
    # Do not expose URL metadata for candidates that the answer never references.
    result = [
        source if source.citation_id in used else source.model_copy(update={
            "citation_id": None, "source_type": None, "source_url": None, "article_title": None,
        })
        for source in sources
    ]
    return answer.strip(), result
