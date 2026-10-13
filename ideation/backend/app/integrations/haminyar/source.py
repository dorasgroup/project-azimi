import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlsplit

import httpx

CMS_API = "https://cms.haminyar.ir/wp-json/wp/v2/posts"
PAGE_SIZE = 50
MAX_PAGES = 100
MAX_RESPONSE_BYTES = 12 * 1024 * 1024
INTERVIEW_CATEGORY_ID = 94


class SourceUnavailable(RuntimeError):
    pass


class _ArticleText(HTMLParser):
    """Preserve paragraph/list boundaries, excluding executable/media markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "iframe", "noscript"}:
            self.ignored_depth += 1
        if not self.ignored_depth and tag in {
            "p",
            "div",
            "br",
            "li",
            "h1",
            "h2",
            "h3",
            "h4",
            "tr",
            "blockquote",
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "iframe", "noscript"} and self.ignored_depth:
            self.ignored_depth -= 1
        if not self.ignored_depth and tag in {"p", "div", "li", "tr", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.ignored_depth:
            self.parts.append(data)


def plain_text(value: str) -> str:
    parser = _ArticleText()
    parser.feed(value)
    text = "".join(parser.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()


def is_article(post: dict) -> bool:
    # Match the public magazine catalog's type rules. The inventory deliberately
    # uses the same title/excerpt fields as the site's article listing.
    signals = (
        f"{post.get('title', {}).get('rendered', '')} {post.get('excerpt', {}).get('rendered', '')}"
    )
    return (
        post.get("status") == "publish"
        and post.get("format") not in {"audio", "video"}
        and INTERVIEW_CATEGORY_ID not in (post.get("categories") or [])
        and not re.search(r"پادکست|podcast|<audio\b|\[audio\b", signals, re.I)
    )


def gmt_date(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=UTC)


def public_url(post: dict) -> str:
    candidate = str(post.get("link") or "")
    parsed = urlsplit(candidate)
    if (
        parsed.scheme == "https"
        and parsed.hostname in {"haminyar.ir", "www.haminyar.ir"}
        and not parsed.username
        and not parsed.password
    ):
        return candidate
    slug = str(post.get("slug") or "")
    if not slug or "/" in unquote(slug) or "\\" in unquote(slug):
        raise SourceUnavailable("invalid public article URL")
    return f"https://haminyar.ir/marriage-guides/{slug}"


@dataclass(frozen=True)
class ArticleMetadata:
    external_id: int
    title: str
    source_url: str
    published_at: datetime | None
    modified_at: datetime | None

    @classmethod
    def from_post(cls, post: dict) -> "ArticleMetadata":
        external_id = int(post["id"])
        title = plain_text(post.get("title", {}).get("rendered", ""))
        if external_id <= 0 or not title or len(title) > 1000:
            raise SourceUnavailable("invalid article metadata")
        return cls(
            external_id,
            title,
            public_url(post),
            gmt_date(post.get("date_gmt")),
            gmt_date(post.get("modified_gmt")),
        )


@dataclass(frozen=True)
class ArticleContent:
    metadata: ArticleMetadata
    text: str

    @property
    def content_hash(self) -> str:
        canonical = f"{self.metadata.title}\n{self.metadata.source_url}\n{self.text}"
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def filename(self) -> str:
        title = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", self.metadata.title).strip(" .")
        return f"haminyar-{self.metadata.external_id}-{title[:90]}.txt"

    def render_txt(self) -> bytes:
        date = self.metadata.published_at.isoformat() if self.metadata.published_at else ""
        return (
            f"عنوان مقاله: {self.metadata.title}\nدسته: ازدواج\n"
            f"منبع: مقالات سایت حامین یار\nنشانی مقاله: {self.metadata.source_url}\n"
            f"شناسه مقاله: {self.metadata.external_id}\nتاریخ انتشار: {date}\n\n"
            f"متن مقاله:\n{self.text}\n"
        ).encode()


class HaminyarArticleSource:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        # Fixed, approved HTTPS origin and no redirects prevent SSRF via post links.
        async with self.client.stream(
            "GET", url, params=params, follow_redirects=False
        ) as response:
            response.raise_for_status()
            parts: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise SourceUnavailable("CMS response is too large")
                parts.append(chunk)
            headers = dict(response.headers)
            # aiter_bytes already decoded gzip/deflate. Do not ask Response to
            # decompress the decoded JSON a second time.
            headers.pop("content-encoding", None)
            headers.pop("content-length", None)
            return httpx.Response(
                response.status_code,
                headers=headers,
                content=b"".join(parts),
                request=response.request,
            )

    async def inventory(self) -> list[ArticleMetadata]:
        params = {
            "status": "publish",
            "per_page": PAGE_SIZE,
            "page": 1,
            "orderby": "id",
            "order": "asc",
            "_fields": "id,slug,status,format,categories,title,excerpt,modified_gmt,date_gmt,link",
        }
        first = await self._get(CMS_API, params)
        try:
            total = int(first.headers["x-wp-total"])
            pages = int(first.headers["x-wp-totalpages"])
            if not 0 <= pages <= MAX_PAGES or total > PAGE_SIZE * MAX_PAGES:
                raise ValueError("unbounded catalog")
            posts = first.json()
            if not isinstance(posts, list):
                raise ValueError("invalid list")
            semaphore = asyncio.Semaphore(4)

            async def get_page(page: int) -> list:
                async with semaphore:
                    response = await self._get(CMS_API, {**params, "page": page})
                    if int(response.headers["x-wp-total"]) != total:
                        raise SourceUnavailable("catalog changed during enumeration")
                    data = response.json()
                    if not isinstance(data, list):
                        raise SourceUnavailable("invalid catalog page")
                    return data

            for page in await asyncio.gather(*(get_page(p) for p in range(2, pages + 1))):
                posts.extend(page)
            ids = [int(post["id"]) for post in posts]
            if len(posts) != total or len(set(ids)) != total:
                raise SourceUnavailable("incomplete catalog")
            return [ArticleMetadata.from_post(post) for post in posts if is_article(post)]
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceUnavailable("invalid or incomplete CMS inventory") from exc

    async def content(self, external_id: int) -> ArticleContent:
        response = await self._get(f"{CMS_API}/{int(external_id)}")
        post = response.json()
        if not is_article(post) or post.get("id") != external_id:
            raise SourceUnavailable("article is no longer public")
        body = plain_text(post.get("content", {}).get("rendered", ""))
        if len(body) < 30:
            raise SourceUnavailable("article has no usable text")
        return ArticleContent(ArticleMetadata.from_post(post), body)
