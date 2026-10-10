"""Small Bale Bot API client; credentials remain process-local settings only."""
from dataclasses import dataclass
import time

import httpx
from django.conf import settings


@dataclass
class BaleResult:
    message_id: str = ""


class BaleClientError(Exception):
    def __init__(self, message, *, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code=status_code
        self.retry_after=retry_after


class BaleClient:
    def __init__(self, *, token=None, http_client=None):
        self.token=settings.BALE_BOT_TOKEN if token is None else token
        self.base_url=settings.BALE_API_BASE_URL
        self.timeout=settings.BALE_HTTP_TIMEOUT_SECONDS
        self.http_client=http_client or httpx.Client()

    def _url(self, method):
        if not self.token:
            raise BaleClientError("توکن ربات بله تنظیم نشده است.")
        return f"{self.base_url}/bot{self.token}/{method}"

    @staticmethod
    def _retry_after(response, payload):
        value=(payload.get("parameters") or {}).get("retry_after") if isinstance(payload,dict) else None
        if value is None:
            value=response.headers.get("Retry-After")
        try:
            return max(0, min(int(value), 60))
        except (TypeError, ValueError):
            return None

    def _request(self, method, payload=None):
        for attempt in range(2):
            try:
                response=(self.http_client.post(self._url(method),json=payload,timeout=self.timeout) if payload is not None else self.http_client.get(self._url(method),timeout=self.timeout))
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt==0:
                    time.sleep(0.1)
                    continue
                raise BaleClientError("ارتباط با API بله برقرار نشد.")
            try:
                data=response.json()
            except (ValueError, TypeError):
                data=None
            retry_after=self._retry_after(response,data)
            if response.status_code==429:
                if attempt==0:
                    time.sleep(min(retry_after or 0.1, 1))
                    continue
                raise BaleClientError("محدودیت ارسال بله اعمال شده است.",status_code=429,retry_after=retry_after)
            if response.status_code in (401,403):
                raise BaleClientError("دسترسی ربات بله پذیرفته نشد.",status_code=response.status_code)
            if response.status_code>=500:
                if attempt==0:
                    time.sleep(0.1)
                    continue
                raise BaleClientError("سرویس بله موقتاً در دسترس نیست.",status_code=response.status_code)
            if response.status_code>=400:
                raise BaleClientError("درخواست ارسال به بله پذیرفته نشد.",status_code=response.status_code)
            if not isinstance(data,dict) or data.get("ok") is not True:
                raise BaleClientError("پاسخ معتبر از API بله دریافت نشد.")
            return data

    def get_me(self):
        return self._request("getMe")

    def send_message(self, chat_id, text):
        data=self._request("sendMessage",{"chat_id":str(chat_id),"text":text})
        result=data.get("result") or {}
        return BaleResult(message_id=str(result.get("message_id") or ""))

    def get_updates(self):
        return self._request("getUpdates")
