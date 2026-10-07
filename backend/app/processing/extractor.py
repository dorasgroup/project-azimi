import json
import re
from collections.abc import Iterable
from typing import Mapping
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.crawler.discovery import canonicalize_url
from app.processing.publication_dates import PublicationDateResolver
from app.processing.text import normalize_text

NOISE_TAGS = {
    "aside",
    "button",
    "footer",
    "form",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
}
NOISE_HINTS = {
    "advert",
    "advertisement",
    "breadcrumb",
    "comment",
    "cookie",
    "footer",
    "header",
    "menu",
    "nav",
    "newsletter",
    "promo",
    "recommend",
    "related",
    "share",
    "sidebar",
    "social",
}

ARTICLE_TYPES = {
    "Article",
    "BlogPosting",
    "LiveBlogPosting",
    "NewsArticle",
    "Report",
}


def _meta_content(soup: BeautifulSoup, names: Iterable[str]) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if isinstance(tag, Tag) and tag.get("content"):
            return normalize_text(str(tag["content"]))
    return None


def _json_ld_types(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {str(item) for item in value}
    return set()


def _json_ld_articles(soup: BeautifulSoup) -> list[dict]:
    articles: list[dict] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if isinstance(candidate, dict) and isinstance(candidate.get("@graph"), list):
                candidates.extend(candidate["@graph"])
            if (
                isinstance(candidate, dict)
                and _json_ld_types(candidate.get("@type")) & ARTICLE_TYPES
            ):
                articles.append(candidate)
    return articles


def _json_ld_text_values(value: object) -> list[str]:
    if isinstance(value, str):
        text = normalize_text(value)
        return [text] if text else []
    if isinstance(value, dict):
        values: list[str] = []
        for key in ("headline", "name", "articleBody", "description", "text"):
            values.extend(_json_ld_text_values(value.get(key)))
        return values
    if isinstance(value, list):
        values: list[str] = []
        for item in value:
            values.extend(_json_ld_text_values(item))
        return values
    return []


def _json_ld_value(article: dict, key: str) -> str | None:
    value = article.get(key)
    if isinstance(value, str):
        return normalize_text(value)
    if isinstance(value, dict):
        return normalize_text(str(value.get("name") or value.get("url") or "")) or None
    if isinstance(value, list):
        values = _json_ld_text_values(value)
        return values[0] if values else None
    return None


def _json_ld_body(article: dict) -> str:
    parts: list[str] = []
    for key in ("articleBody", "description", "abstract"):
        parts.extend(_json_ld_text_values(article.get(key)))
    updates = article.get("liveBlogUpdate")
    if isinstance(updates, dict):
        updates = [updates]
    if isinstance(updates, list):
        for update in updates:
            if not isinstance(update, dict):
                continue
            headline = _json_ld_value(update, "headline")
            if headline:
                parts.append(headline)
            for key in ("articleBody", "description"):
                parts.extend(_json_ld_text_values(update.get(key)))
    return normalize_text("\n\n".join(dict.fromkeys(part for part in parts if len(part) >= 20)))


def _remove_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.attrs is None:
            continue
        marker = " ".join(
            [
                str(tag.get("id") or ""),
                " ".join(str(value) for value in tag.get("class") or []),
                str(tag.get("role") or ""),
            ]
        ).casefold()
        marker_tokens = set(re.findall(r"[a-z0-9]+", marker))
        if marker_tokens & NOISE_HINTS:
            tag.decompose()


def extract_article(
    html: str,
    url: str,
    fallback_title: str | None = None,
    page_metadata: Mapping[str, object] | None = None,
) -> dict:
    soup = BeautifulSoup(html, "lxml")
    articles = _json_ld_articles(soup)
    article = articles[0] if articles else {}

    canonical_tag = soup.find("link", attrs={"rel": lambda value: value and "canonical" in value})
    canonical_href = canonical_tag.get("href") if isinstance(canonical_tag, Tag) else None
    canonical_url = canonicalize_url(urljoin(url, str(canonical_href or url)))

    title = (
        _json_ld_value(article, "headline")
        or _meta_content(soup, ("og:title", "twitter:title"))
        or normalize_text(fallback_title or "")
        or normalize_text(soup.title.string if soup.title and soup.title.string else "")
    )
    author = _json_ld_value(article, "author") or _meta_content(soup, ("author", "article:author"))
    source_name = _json_ld_value(article, "publisher") or _meta_content(soup, ("og:site_name",))
    meta_description = _meta_content(soup, ("description", "og:description", "twitter:description"))
    date_resolution = PublicationDateResolver().resolve(soup, url, articles, page_metadata)
    image = _json_ld_value(article, "image") or _meta_content(soup, ("og:image", "twitter:image"))
    structured_text = _json_ld_body(article)

    _remove_noise(soup)
    container = soup.find("article") or soup.find("main") or soup.body or soup
    paragraphs = [
        normalize_text(element.get_text(" ", strip=True))
        for element in container.find_all(["p", "h2", "h3", "blockquote", "li"])
    ]
    paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 20]
    clean_text = normalize_text("\n\n".join(dict.fromkeys(paragraphs)))
    if not clean_text:
        clean_text = normalize_text(container.get_text("\n", strip=True))
    if len(clean_text) < 200:
        clean_text = normalize_text(
            "\n\n".join(
                dict.fromkeys(
                    part
                    for part in (clean_text, structured_text, meta_description or "")
                    if len(part) >= 20
                )
            )
        )

    return {
        "canonical_url": canonical_url,
        "title": title,
        "clean_text": clean_text,
        "author": author,
        "source_name": source_name,
        "published_at": date_resolution.published_at if date_resolution else None,
        "published_at_source": date_resolution.source if date_resolution else None,
        "published_at_confidence": date_resolution.confidence if date_resolution else None,
        "published_at_raw": date_resolution.raw_value if date_resolution else None,
        "publication_date_candidates": len(date_resolution.candidates) if date_resolution else 0,
        "lead_image_url": urljoin(url, image) if image else None,
    }
