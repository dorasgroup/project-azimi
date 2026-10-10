"""Best-effort adapters for public content sources.

Telegram's public page markup is not a stable API. This adapter deliberately
uses no Telegram credential and may need parser updates when that markup changes.
"""

import re
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings


@dataclass(frozen=True)
class CollectedMessage:
    external_id: str
    published_at: datetime
    raw_text: str
    public_url: str = ""
    content_type: str = "message"
    metadata: dict | None = None
    media_url: str = ""


@dataclass(frozen=True)
class CollectionResult:
    messages: list[CollectedMessage] = field(default_factory=list)
    discovered_name: str = ""
    parse_error_count: int = 0
    safe_cursor_external_id: str = ""
    safe_cursor_published_at: datetime | None = None
    update_existing: bool = False


class BaseCollector:
    def collect(self, source, cursor) -> CollectionResult:
        raise NotImplementedError


class TelegramCollector(BaseCollector):
    """Credential-free, best-effort parser for public ``t.me/s/<channel>`` pages."""

    MAX_PAGES = 50

    _MESSAGE_WRAP = re.compile(r'<div class="tgme_widget_message_wrap', re.I)
    _POST_ID = re.compile(r'data-post="[^/"]+/([0-9]+)"', re.I)
    _DATE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.I)
    _TEXT = re.compile(r'tgme_widget_message_text[^>]*>(.*?)</div>', re.I | re.S)
    _VIEWS = re.compile(r'tgme_widget_message_views[^>]*>([\d,.KMB]+)', re.I)
    _MEDIA = re.compile(r"background-image:url\('([^']+)'\)", re.I)
    _CHANNEL_TITLE = re.compile(r'tgme_channel_info_header_title[^>]*>.*?<span[^>]*>(.*?)</span>', re.I | re.S)
    _OG_TITLE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)', re.I)

    def fetch(self, url):
        request = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "X-Trend-Collector": "1",
        })
        ssl_context = ssl.create_default_context()
        ssl_context.maximum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.set_alpn_protocols(["http/1.1"])
        try:
            with urlopen(request, timeout=20, context=ssl_context) as response:
                return response.read().decode("utf-8", errors="replace")
        except URLError as exc:
            raise RuntimeError("PublicChannelUnavailable") from exc

    @staticmethod
    def _text_from_html(value):
        if not value:
            return ""
        text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
        text = re.sub(r"</(p|div|h\d)>", "\n", text, flags=re.I)
        return re.sub(r"<.*?>", "", unescape(text)).strip()

    @staticmethod
    def _safe_http_url(value):
        parsed = urlparse(unescape(value or ""))
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    def _channel_name(self, html):
        match = self._CHANNEL_TITLE.search(html)
        if match:
            return self._text_from_html(match.group(1))
        match = self._OG_TITLE.search(html)
        if match:
            return self._text_from_html(match.group(1)).removesuffix(" | Telegram").strip()
        return ""

    def collect(self, source, cursor):
        return self._collect(source, cursor)

    def collect_window(self, source, cursor, start_at, end_at):
        return self._collect(source, cursor, start_at=start_at, end_at=end_at)

    def _collect(self, source, cursor, *, start_at=None, end_at=None):
        base_url = getattr(settings, "TELEGRAM_BASE_URL", "https://t.me").rstrip("/")
        try:
            previous_cursor = int(cursor.last_external_id or 0)
        except (TypeError, ValueError):
            previous_cursor = 0

        parsed: list[tuple[int, CollectedMessage | None]] = []
        unknown_position_error = False
        parse_error_count = 0
        discovered_name = ""
        forward = previous_cursor > 0
        page_marker = previous_cursor if forward else None
        complete = False
        for _ in range(self.MAX_PAGES):
            url = f"{base_url}/s/{source.identifier}"
            if page_marker is not None:
                url += f"?{'after' if forward else 'before'}={page_marker}"
            html = self.fetch(url)
            if not discovered_name:
                discovered_name = self._channel_name(html)
            if "tme_no_messages_found" in html and discovered_name:
                complete = True
                break
            starts = [match.start() for match in self._MESSAGE_WRAP.finditer(html)]
            if not starts:
                if forward and discovered_name:
                    complete = True
                    break
                parse_error_count += 1
                break
            page_ids = []
            page_dates = []
            reached_end = False
            for index, start in enumerate(starts):
                block = html[start : starts[index + 1] if index + 1 < len(starts) else len(html)]
                post_match = self._POST_ID.search(block)
                if not post_match:
                    parse_error_count += 1
                    unknown_position_error = True
                    continue
                post_id = int(post_match.group(1))
                page_ids.append(post_id)
                if post_id <= previous_cursor or (forward and page_marker is not None and post_id <= page_marker):
                    continue
                try:
                    date_match = self._DATE.search(block)
                    if not date_match:
                        raise ValueError("missing datetime")
                    published_at = datetime.fromisoformat(date_match.group(1).replace("Z", "+00:00"))
                    page_dates.append(published_at)
                    if end_at and published_at > end_at:
                        reached_end = True
                        continue
                    text_match = self._TEXT.search(block)
                    views_match = self._VIEWS.search(block)
                    media_match = self._MEDIA.search(block)
                    media_url = self._safe_http_url(media_match.group(1)) if media_match else ""
                    parsed.append((post_id, CollectedMessage(
                        external_id=str(post_id),
                        published_at=published_at,
                        raw_text=self._text_from_html(text_match.group(1)) if text_match else "",
                        public_url=f"https://t.me/{source.identifier}/{post_id}",
                        content_type="media" if media_url else "message",
                        metadata={"views": views_match.group(1) if views_match else ""},
                        media_url=media_url,
                    )))
                except (TypeError, ValueError, OverflowError):
                    parse_error_count += 1
                    parsed.append((post_id, None))
            next_marker = max(page_ids, default=None) if forward else min(page_ids, default=None)
            if forward and page_marker is not None and next_marker is not None and next_marker <= page_marker:
                complete = True
                break
            if next_marker is None or (page_marker is not None and not forward and next_marker >= page_marker):
                parse_error_count += 1
                break
            if reached_end or (not forward and ((start_at and page_dates and min(page_dates) < start_at) or start_at is None)):
                complete = True
                break
            page_marker = next_marker
        if not complete:
            parse_error_count = max(1, parse_error_count)

        parsed.sort(key=lambda pair: pair[0])
        messages = [message for _, message in parsed if message is not None]
        safe_messages = []
        if complete and not unknown_position_error:
            for _, message in parsed:
                if message is None:
                    break
                safe_messages.append(message)
        safe_cursor = safe_messages[-1] if safe_messages else None
        return CollectionResult(
            messages=messages,
            discovered_name=discovered_name,
            parse_error_count=parse_error_count,
            safe_cursor_external_id=safe_cursor.external_id if safe_cursor else "",
            safe_cursor_published_at=safe_cursor.published_at if safe_cursor else None,
        )
