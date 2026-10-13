from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from app.core.config import settings


class BaleAPIError(RuntimeError):
    """A deliberately redacted Bale API failure."""


class BaleFileTooLargeError(BaleAPIError):
    """The file exceeds the configured Bale download limit."""


class BaleClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client

    async def _request(self, method: str, payload: Mapping[str, Any] | None = None) -> Any:
        token = settings.bale_bot_token_value
        if not token:
            raise BaleAPIError("Bale bot token is not configured")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        url = f"{str(settings.bale_api_base_url).rstrip('/')}/bot{token}/{method}"
        try:
            response = await client.post(url, json=dict(payload or {}))
            try:
                body = response.json()
            except ValueError as exc:
                raise BaleAPIError("Bale API returned an invalid response") from exc
            if response.status_code >= 400 or not body.get("ok"):
                code = body.get("error_code", response.status_code)
                raise BaleAPIError(f"Bale API request failed with code {code}")
            return body.get("result")
        except httpx.HTTPError as exc:
            raise BaleAPIError("Could not connect to Bale API") from exc
        finally:
            if owns_client:
                await client.aclose()

    async def get_me(self) -> dict[str, Any]:
        return await self._request("getMe")

    async def get_webhook_info(self) -> dict[str, Any]:
        return await self._request("getWebhookInfo")

    async def get_file(self, file_id: str) -> dict[str, Any]:
        result = await self._request("getFile", {"file_id": file_id})
        if not isinstance(result, dict) or not result.get("file_path"):
            raise BaleAPIError("Bale API returned invalid file metadata")
        return result

    async def download_file(self, file_id: str, *, max_size_bytes: int) -> bytes:
        metadata = await self.get_file(file_id)
        file_path = str(metadata.get("file_path") or "")
        parsed = urlparse(file_path)
        path = PurePosixPath(file_path)
        if (
            not file_path
            or parsed.scheme
            or parsed.netloc
            or file_path.startswith("/")
            or "\\" in file_path
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise BaleAPIError("Bale API returned an unsafe file path")
        reported_size = metadata.get("file_size")
        if isinstance(reported_size, int) and reported_size > max_size_bytes:
            raise BaleFileTooLargeError("Bale file exceeds the download limit")

        token = settings.bale_bot_token_value
        if not token:
            raise BaleAPIError("Bale bot token is not configured")
        encoded_path = "/".join(quote(part, safe="") for part in path.parts)
        url = (
            f"{str(settings.bale_api_base_url).rstrip('/')}"
            f"/file/bot{token}/{encoded_path}"
        )
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        chunks: list[bytes] = []
        total_size = 0
        try:
            async with client.stream("GET", url, follow_redirects=False) as response:
                if response.status_code >= 300:
                    raise BaleAPIError(
                        f"Bale file download failed with code {response.status_code}"
                    )
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > max_size_bytes:
                    raise BaleFileTooLargeError("Bale file exceeds the download limit")
                async for chunk in response.aiter_bytes(1024 * 1024):
                    total_size += len(chunk)
                    if total_size > max_size_bytes:
                        raise BaleFileTooLargeError("Bale file exceeds the download limit")
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise BaleAPIError("Could not download file from Bale API") from exc
        finally:
            if owns_client:
                await client.aclose()
        return b"".join(chunks)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text[:4096]}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        return await self._request("sendMessage", payload)

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> bool:
        return bool(await self._request("sendChatAction", {"chat_id": chat_id, "action": action}))

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any] | bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text[:4096],
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._request("editMessageText", payload)

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> bool:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text[:200]
        return bool(await self._request("answerCallbackQuery", payload))
