import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from defusedxml import ElementTree

from app.crawler.schemas import DiscoveredLink
from app.crawler.security import host_is_allowed
from app.processing.text import normalize_text, tokens
from app.processing.topic import concept_hits, topic_concepts

NON_CONTENT_PARTS = {
    "about",
    "advertise",
    "archive",
    "author",
    "category",
    "contact",
    "login",
    "privacy",
    "search",
    "tag",
    "terms",
}

TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


@dataclass(frozen=True)
class SitemapReference:
    url: str
    last_modified: datetime | None = None


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            urlencode(sorted(query_items)),
            "",
        )
    )


def topic_terms(topic: str) -> set[str]:
    return {
        term.casefold()
        for term in topic.replace("،", " ").replace(",", " ").split()
        if len(term) >= 2
    }


def link_matches_topic(url: str, title: str | None, topic: str) -> list[str]:
    concepts = topic_concepts(topic)
    path = urlparse(url).path.replace("-", " ")
    title_text = title or ""
    path_tokens = set(tokens(path))
    title_tokens = set(tokens(title_text))
    path_normalized = normalize_text(path).casefold()
    title_normalized = normalize_text(title_text).casefold()
    matches: set[str] = set()
    for concept in concepts:
        hits = concept_hits(
            concept,
            token_sets=[path_tokens, title_tokens],
            texts=[path_normalized, title_normalized],
        )
        if hits:
            matches.add(concept.label)
    return sorted(matches)


def is_probable_content_url(url: str) -> bool:
    parsed = urlparse(url)
    path_parts = {part.casefold() for part in parsed.path.split("/") if part}
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
    return not bool(path_parts & NON_CONTENT_PARTS) and not bool(
        query_keys & {"page", "paged", "pagenum", "offset", "start"}
    )


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str | None]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            title = " ".join(" ".join(self._text).split()) or None
            self.links.append((self._href, title))
            self._href = None
            self._text = []


class NavigationLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str, str]] = []
        self._href: str | None = None
        self._rel = ""
        self._class_name = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "link" and values.get("href"):
            self.links.append(
                (values["href"], "", values.get("rel", ""), values.get("class", ""))
            )
        elif tag.casefold() == "a" and values.get("href"):
            self._href = values["href"]
            self._rel = values.get("rel", "")
            self._class_name = values.get("class", "")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "a" and self._href:
            text = " ".join(" ".join(self._text).split())
            self.links.append((self._href, text, self._rel, self._class_name))
            self._href = None
            self._rel = ""
            self._class_name = ""
            self._text = []


def discover_navigation_links(
    html: str,
    base_url: str,
    allow_subdomains: bool,
) -> list[str]:
    parser = NavigationLinkParser()
    parser.feed(html)
    links: list[str] = []
    seen: set[str] = set()
    for href, text, rel, class_name in parser.links:
        url = canonicalize_url(urljoin(base_url, href))
        parsed = urlparse(url)
        query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query)}
        normalized_text = normalize_text(text).casefold()
        navigation_hint = " ".join((rel, class_name, normalized_text)).casefold()
        is_navigation = (
            "next" in rel.casefold().split()
            or bool(query_keys & {"page", "paged", "pagenum", "offset", "start"})
            or bool(re.search(r"/(?:page|paged)/?\d+/?$", parsed.path.casefold()))
            or any(
                marker in navigation_hint
                for marker in ("pagination", "pager", "next", "بعدی", "صفحه بعد")
            )
        )
        if (
            not is_navigation
            or url in seen
            or not host_is_allowed(url, base_url, allow_subdomains)
        ):
            continue
        seen.add(url)
        links.append(url)
    return links


def discover_from_html(
    html: str,
    base_url: str,
    topic: str,
    allow_subdomains: bool,
    via: str = "html",
) -> list[DiscoveredLink]:
    parser = LinkParser()
    parser.feed(html)
    links: list[DiscoveredLink] = []
    seen: set[str] = set()
    canonical_base = canonicalize_url(base_url)
    for href, title in parser.links:
        url = canonicalize_url(urljoin(base_url, href))
        if (
            url in seen
            or url == canonical_base
            or not host_is_allowed(url, base_url, allow_subdomains)
            or not is_probable_content_url(url)
        ):
            continue
        matches = link_matches_topic(url, title, topic)
        seen.add(url)
        links.append(
            DiscoveredLink(
                url=url,
                title=title,
                discovered_via=via,
                topic_matches=matches,
                discovery_methods=[via],
            )
        )
    return links


def _element_text(element: Any, names: set[str]) -> str | None:
    for child in element.iter():
        if child.tag.rsplit("}", 1)[-1].casefold() in names and child.text:
            return child.text.strip()
    return None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


def discover_from_xml(
    xml: str,
    source_url: str,
    topic: str,
    via: str,
    allow_subdomains: bool,
) -> list[DiscoveredLink]:
    root = ElementTree.fromstring(xml)
    links: list[DiscoveredLink] = []
    seen: set[str] = set()
    for item in root.iter():
        local_name = item.tag.rsplit("}", 1)[-1].casefold()
        if local_name not in {"item", "entry", "url"}:
            continue
        url = _element_text(item, {"link", "loc"})
        if not url:
            link_element = next(
                (
                    child
                    for child in item.iter()
                    if child.tag.rsplit("}", 1)[-1].casefold() == "link"
                ),
                None,
            )
            url = link_element.attrib.get("href") if link_element is not None else None
        if not url:
            continue
        url = canonicalize_url(urljoin(source_url, url))
        if url in seen or not host_is_allowed(url, source_url, allow_subdomains):
            continue
        title = _element_text(item, {"title"})
        matches = link_matches_topic(url, title, topic)
        seen.add(url)
        links.append(
            DiscoveredLink(
                url=url,
                title=title,
                discovered_via=via,
                published_at=_parse_date(
                    _element_text(item, {"pubdate", "published", "updated", "lastmod"})
                ),
                topic_matches=matches,
                discovery_methods=[via],
            )
        )
    return links


def discover_sitemap_locations(
    xml: str,
    source_url: str,
    allow_subdomains: bool,
) -> list[str]:
    return [
        reference.url
        for reference in discover_sitemap_references(xml, source_url, allow_subdomains)
    ]


def discover_sitemap_references(
    xml: str,
    source_url: str,
    allow_subdomains: bool,
) -> list[SitemapReference]:
    root = ElementTree.fromstring(xml)
    if root.tag.rsplit("}", 1)[-1].casefold() != "sitemapindex":
        return []
    references: list[SitemapReference] = []
    seen: set[str] = set()
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1].casefold() != "sitemap":
            continue
        url = _element_text(item, {"loc"})
        if not url:
            continue
        url = canonicalize_url(urljoin(source_url, url))
        if url in seen or not host_is_allowed(url, source_url, allow_subdomains):
            continue
        seen.add(url)
        references.append(
            SitemapReference(
                url=url,
                last_modified=_parse_date(_element_text(item, {"lastmod"})),
            )
        )
    return references


def discover_sitemaps_from_robots(
    robots_text: str,
    source_url: str,
    allow_subdomains: bool,
) -> list[str]:
    locations: list[str] = []
    seen: set[str] = set()
    for raw_line in robots_text.splitlines():
        key, separator, value = raw_line.partition(":")
        if not separator or key.strip().casefold() != "sitemap":
            continue
        location = value.split("#", 1)[0].strip()
        if not location:
            continue
        url = canonicalize_url(urljoin(source_url, location))
        if url in seen or not host_is_allowed(url, source_url, allow_subdomains):
            continue
        seen.add(url)
        locations.append(url)
    return locations
