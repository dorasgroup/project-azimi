"""Publication-date candidate extraction and confidence-based resolution."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Mapping, Sequence
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, Tag

from app.processing.date_parsing import PERSIAN_MONTHS, normalize_date_text, parse_date_value
PUBLICATION_META_KEYS = {
    "article:published_time": ("meta.article:published_time", 0.96),
    "datepublished": ("meta.datePublished", 0.93),
    "publish-date": ("meta.publish-date", 0.9),
    "pubdate": ("meta.pubdate", 0.9),
    "parsely-pub-date": ("meta.parsely-pub-date", 0.9),
    "sailthru.date": ("meta.sailthru.date", 0.88),
    "date": ("meta.date", 0.82),
}
MODIFIED_META_KEYS = {
    "article:modified_time": ("meta.article:modified_time", 0.45),
    "datemodified": ("meta.dateModified", 0.42),
}


@dataclass(frozen=True)
class DateCandidate:
    value: datetime
    source: str
    raw_value: str
    confidence: float


@dataclass(frozen=True)
class ResolvedPublicationDate:
    published_at: datetime
    source: str
    raw_value: str
    confidence: float
    candidates: tuple[DateCandidate, ...]


def _plausible(value: datetime) -> bool:
    now = datetime.now(UTC)
    return datetime(1990, 1, 1, tzinfo=UTC) <= value <= now + timedelta(days=2)


class PublicationDateResolver:
    """Collect publication candidates and select the most reliable plausible date."""

    def resolve(
        self,
        soup: BeautifulSoup,
        url: str,
        json_ld_articles: Sequence[Mapping[str, object]],
        page_metadata: Mapping[str, object] | None = None,
    ) -> ResolvedPublicationDate | None:
        candidates: list[DateCandidate] = []

        def add(raw: object, source: str, confidence: float) -> None:
            if raw is None:
                return
            raw_value = normalize_date_text(str(raw))
            parsed = parse_date_value(raw_value)
            if parsed is None or not _plausible(parsed):
                return
            candidates.append(DateCandidate(parsed, source, raw_value, confidence))

        for article in json_ld_articles:
            add(article.get("datePublished"), "jsonld.datePublished", 0.99)
            add(article.get("coverageStartTime"), "jsonld.coverageStartTime", 0.9)
            add(article.get("dateCreated"), "jsonld.dateCreated", 0.82)
            add(article.get("dateModified"), "jsonld.dateModified", 0.45)

        for tag in soup.find_all("meta"):
            if not isinstance(tag, Tag) or not tag.get("content"):
                continue
            key = str(tag.get("property") or tag.get("name") or tag.get("itemprop") or "")
            key = key.casefold()
            specification = PUBLICATION_META_KEYS.get(key) or MODIFIED_META_KEYS.get(key)
            if specification:
                add(tag.get("content"), *specification)

        for tag in soup.find_all("time"):
            if not isinstance(tag, Tag):
                continue
            raw = tag.get("datetime") or tag.get("content") or tag.get_text(" ", strip=True)
            marker = " ".join(
                (
                    str(tag.get("itemprop") or ""),
                    str(tag.get("class") or ""),
                    str(tag.get("id") or ""),
                )
            ).casefold()
            confidence = 0.88 if any(
                hint in marker for hint in ("datepublished", "publish", "posted", "تاریخ")
            ) else 0.62
            add(raw, "html.time", confidence)

        if page_metadata:
            discovered = page_metadata.get("discovered_published_at")
            discovered_via = str(page_metadata.get("discovered_via") or "")
            confidence = 0.88 if discovered_via == "rss" else 0.56
            add(discovered, f"discovery.{discovered_via or 'unknown'}", confidence)
            for key in ("published_at", "datePublished", "publication_date"):
                add(page_metadata.get(key), f"crawler_metadata.{key}", 0.78)

        decoded_path = unquote(urlparse(url).path)
        add(decoded_path, "url.date", 0.72)

        visible_text = normalize_date_text(soup.get_text(" ", strip=True)[:100_000])
        label_pattern = re.compile(
            r"(?:تاریخ\s*(?:انتشار|نشر)|زمان\s*انتشار|منتشر\s*شده|"
            r"published(?:\s+on)?|posted(?:\s+on)?)\s*[:：-]?\s*"
            r"(?P<date>(?:1[34]\d{2}|(?:19|20)\d{2})\s*[/.-]\s*\d{1,2}"
            r"\s*[/.-]\s*\d{1,2}|\d{1,2}\s+(?:"
            + "|".join(PERSIAN_MONTHS)
            + r")\s+1[34]\d{2})",
            re.IGNORECASE,
        )
        text_match = label_pattern.search(visible_text)
        if text_match:
            add(text_match.group("date"), "visible_text.labelled_date", 0.68)

        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item.confidence, item.source, item.raw_value))
        best = candidates[0]
        agreeing_sources = {
            candidate.source
            for candidate in candidates
            if candidate.value.date() == best.value.date()
        }
        confidence = min(1.0, best.confidence + (0.02 if len(agreeing_sources) > 1 else 0.0))
        return ResolvedPublicationDate(
            published_at=best.value,
            source=best.source,
            raw_value=best.raw_value,
            confidence=confidence,
            candidates=tuple(candidates),
        )
