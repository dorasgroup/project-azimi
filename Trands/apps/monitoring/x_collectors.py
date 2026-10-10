"""Official X API v2 collector for recent posts from registered accounts only."""

from datetime import timezone as datetime_timezone, timedelta
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.utils import timezone

from .collectors import BaseCollector, CollectedMessage, CollectionResult


class XCollectorError(RuntimeError):
    def __init__(self, safe_message, *, partial=False):
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.partial = partial


class XRecentSearchCollector(BaseCollector):
    """Collect recent original posts through the official ``/2/tweets/search/recent`` endpoint."""

    endpoint = "/tweets/search/recent"
    max_retries = 2

    def __init__(self, client=None):
        self.client = client

    @staticmethod
    def account_query(handle):
        return f"from:{handle} -is:retweet -is:reply"

    @staticmethod
    def _safe_media_url(value):
        parsed = urlparse(value or "")
        return value if parsed.scheme in {"http", "https"} and parsed.netloc else ""

    def _request(self, params):
        token = settings.X_BEARER_TOKEN
        if not token:
            raise XCollectorError("توکن API X تنظیم نشده است.")
        headers = {"Authorization": f"Bearer {token}"}
        url = f"{settings.X_API_BASE_URL}{self.endpoint}"
        owns_client = self.client is None
        client = self.client or httpx.Client(timeout=settings.X_HTTP_TIMEOUT_SECONDS)
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = client.get(url, params=params, headers=headers)
                except httpx.RequestError as exc:
                    if attempt == self.max_retries:
                        raise XCollectorError("ارتباط با API X موقتاً در دسترس نیست.") from exc
                    continue
                if response.status_code == 429:
                    reset = response.headers.get("x-rate-limit-reset")
                    detail = "محدودیت درخواست API X برقرار است"
                    if reset and reset.isdigit():
                        detail += f"؛ زمان بازنشانی: {reset}"
                    raise XCollectorError(detail, partial=True)
                if response.status_code in {401, 403}:
                    raise XCollectorError("اعتبار یا سطح دسترسی API X قابل استفاده نیست.")
                if response.status_code >= 500:
                    if attempt == self.max_retries:
                        raise XCollectorError("سرویس API X موقتاً در دسترس نیست.")
                    continue
                if response.status_code >= 400:
                    raise XCollectorError("درخواست API X ناموفق بود.")
                try:
                    return response.json()
                except ValueError as exc:
                    raise XCollectorError("پاسخ API X قابل پردازش نیست.", partial=True) from exc
        finally:
            if owns_client:
                client.close()

    def _params(self, source, cursor, next_token=None, start_at=None, end_at=None):
        params = {
            "query": self.account_query(source.identifier),
            "max_results": min(settings.X_MAX_POSTS_PER_SOURCE, 100),
            "tweet.fields": "created_at,author_id,public_metrics,attachments,edit_history_tweet_ids",
            "expansions": "attachments.media_keys",
            "media.fields": "url,preview_image_url",
        }
        if next_token:
            params["next_token"] = next_token
        elif cursor.last_external_id:
            params["since_id"] = cursor.last_external_id
        elif start_at:
            params["start_time"] = start_at.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z")
            if end_at:
                params["end_time"] = end_at.astimezone(datetime_timezone.utc).isoformat().replace("+00:00", "Z")
        else:
            start = timezone.now().astimezone(datetime_timezone.utc) - timedelta(hours=settings.X_INITIAL_LOOKBACK_HOURS)
            params["start_time"] = start.isoformat().replace("+00:00", "Z")
        return params

    def _collect(self, source, cursor, start_at=None, end_at=None):
        messages, parse_errors, next_token = [], 0, None
        newest = None
        remaining = settings.X_MAX_POSTS_PER_SOURCE
        while remaining > 0:
            payload = self._request(self._params(source, cursor, next_token, start_at, end_at))
            includes = payload.get("includes") or {}
            media_by_key = {item.get("media_key"): item for item in includes.get("media", []) if item.get("media_key")}
            for tweet in payload.get("data") or []:
                try:
                    post_id, created_at = str(tweet["id"]), tweet["created_at"]
                    published_at = __import__("datetime").datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    metrics = tweet.get("public_metrics") or {}
                    media_url = ""
                    for media_key in (tweet.get("attachments") or {}).get("media_keys") or []:
                        media = media_by_key.get(media_key) or {}
                        media_url = self._safe_media_url(media.get("url") or media.get("preview_image_url"))
                        if media_url:
                            break
                    messages.append(CollectedMessage(
                        external_id=post_id,
                        published_at=published_at,
                        raw_text=tweet.get("text") or "",
                        public_url=f"https://x.com/{source.identifier}/status/{post_id}",
                        content_type="media" if media_url else "post",
                        media_url=media_url,
                        metadata={
                            "username": source.identifier,
                            "author_id": str(tweet.get("author_id") or ""),
                            "like_count": metrics.get("like_count", 0),
                            "repost_count": metrics.get("retweet_count", 0),
                            "reply_count": metrics.get("reply_count", 0),
                            "quote_count": metrics.get("quote_count", 0),
                            "view_count": metrics.get("impression_count", 0),
                        },
                    ))
                    newest = max((newest, messages[-1]), key=lambda item: int(item.external_id)) if newest else messages[-1]
                except (KeyError, TypeError, ValueError, OverflowError):
                    parse_errors += 1
            remaining = settings.X_MAX_POSTS_PER_SOURCE - len(messages)
            next_token = (payload.get("meta") or {}).get("next_token")
            if not next_token or remaining <= 0:
                break
        messages.sort(key=lambda item: int(item.external_id))
        safe_cursor = newest if not parse_errors else None
        return CollectionResult(
            messages=messages,
            parse_error_count=parse_errors,
            safe_cursor_external_id=safe_cursor.external_id if safe_cursor else "",
            safe_cursor_published_at=safe_cursor.published_at if safe_cursor else None,
            update_existing=True,
        )

    def collect(self, source, cursor):
        return self._collect(source, cursor)

    def collect_window(self, source, cursor, start_at, end_at):
        return self._collect(source, cursor, start_at, end_at)
