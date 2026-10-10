"""Small server-to-server client for the Idea Platform trend receiver."""

from dataclasses import dataclass

import httpx
from django.conf import settings


@dataclass
class IdeaClientError(Exception):
    message: str
    retryable: bool
    status_code: int | None = None

    def __str__(self):
        return self.message


class IdeaClient:
    def __init__(self, *, token=None, http_client=None):
        self.token = settings.IDEA_API_TOKEN if token is None else token
        self.http_client = http_client

    def _request(self, method, path, **kwargs):
        headers = dict(kwargs.pop("headers", {}))
        headers["X-Integration-Token"] = self.token
        try:
            if self.http_client is not None:
                response = self.http_client.request(method, path, headers=headers, **kwargs)
            else:
                with httpx.Client(
                    base_url=settings.IDEA_API_BASE_URL,
                    timeout=settings.IDEA_HTTP_TIMEOUT_SECONDS,
                ) as client:
                    response = client.request(method, path, headers=headers, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise IdeaClientError("ارتباط با سامانه ایده‌پردازی برقرار نشد.", True) from exc

        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            message = (
                "سامانه ایده‌پردازی موقتاً در دسترس نیست."
                if retryable
                else "سامانه ایده‌پردازی درخواست را نپذیرفت."
            )
            raise IdeaClientError(message, retryable, response.status_code)
        try:
            data = response.json()
        except (TypeError, ValueError) as exc:
            raise IdeaClientError("پاسخ سامانه ایده‌پردازی معتبر نبود.", False) from exc
        if not isinstance(data, dict):
            raise IdeaClientError("پاسخ سامانه ایده‌پردازی معتبر نبود.", False)
        return data

    def send_trend(self, payload, idempotency_key):
        return self._request(
            "POST",
            "/integrations/trends",
            headers={"Idempotency-Key": idempotency_key},
            json=payload,
        )

    def get_trend_status(self, source_trend_id, source_version=1):
        return self._request(
            "GET",
            f"/integrations/trends/{source_trend_id}",
            params={
                "source_system": settings.IDEA_SOURCE_SYSTEM,
                "source_version": source_version,
            },
        )
