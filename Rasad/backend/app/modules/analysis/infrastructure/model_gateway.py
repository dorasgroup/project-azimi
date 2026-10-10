"""OpenAI-compatible implementation of the provider-neutral ModelGateway port."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from json_repair import repair_json

from app.modules.analysis.domain import ModelGenerationRequest, ModelGenerationResult
from app.modules.platform_settings.domain import ModelProviderRuntime
from app.modules.platform_settings.infrastructure.provider_queries import (
    get_active_model_provider,
)


class ModelGatewayError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelOutputTruncatedError(ModelGatewayError):
    """A length-limited response is incomplete, even if syntax can be repaired."""


class OpenAICompatibleModelGateway:
    def __init__(
        self,
        provider: ModelProviderRuntime | None = None,
        *,
        timeout_seconds: float = 90.0,
        invalid_json_attempts: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider or get_active_model_provider()
        self.timeout_seconds = timeout_seconds
        self.invalid_json_attempts = max(1, invalid_json_attempts)
        self.transport = transport

    async def generate_json(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        # Compatibility fallback and JSON repair share one request deadline;
        # otherwise one attempt can silently multiply the configured timeout.
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._generate_json(request)
        except TimeoutError:
            raise ModelGatewayError("Model generation deadline exceeded", retryable=True) from None

    async def _generate_json(self, request: ModelGenerationRequest) -> ModelGenerationResult:
        payload: dict[str, Any] = {
            "model": self.provider.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
            "response_format": self._response_format(request),
        }
        for attempt in range(self.invalid_json_attempts):
            response = await self._post_compatible(payload)
            if response.status_code >= 400:
                raise ModelGatewayError(
                    f"Model provider {self.provider.provider_id} returned HTTP "
                    f"{response.status_code}",
                    retryable=response.status_code == 429 or response.status_code >= 500,
                )
            try:
                body = response.json()
            except ValueError:
                raise ModelGatewayError("Model provider response was not JSON") from None
            choices = body.get("choices", []) if isinstance(body, dict) else []
            if isinstance(choices, list) and any(isinstance(choice, dict) and choice.get("finish_reason") == "length" for choice in choices):
                # Never turn a partial analysis into successful JSON or send its
                # raw text to syntax repair. The typed adapter owns regeneration.
                raise ModelOutputTruncatedError("Model output exceeded the frozen response budget")
            content = self._response_content(response)
            try:
                parsed = self._parse_json_object(content)
            except ModelGatewayError:
                if attempt + 1 < self.invalid_json_attempts:
                    payload = self._json_repair_payload(request, content)
                    continue
                raise
            return ModelGenerationResult(
                content=parsed,
                provider_id=self.provider.provider_id,
                model=self.provider.model,
            )
        raise ModelGatewayError("Model response contained invalid JSON")

    @staticmethod
    def _response_format(request: ModelGenerationRequest) -> dict[str, Any]:
        if request.output_schema is not None:
            return {"type": "json_schema", "json_schema": {
                "name": "native_analysis", "strict": True, "schema": dict(request.output_schema),
            }}
        return {"type": "json_object"}

    async def _post_compatible(self, payload: dict[str, Any]) -> httpx.Response:
        # Only explicit capability rejection permits fallback. An invalid schema,
        # authentication failure or provider outage must not disable constraints.
        for _ in range(3):
            response = await self._post(payload)
            message = response.text.casefold() if response.status_code == 400 else ""
            unsupported = (any(word in message for word in ("unsupported", "not supported", "does not support", "unknown parameter"))
                           and any(word in message for word in ("response_format", "json_schema")))
            if not unsupported or "response_format" not in payload:
                return response
            if payload["response_format"].get("type") == "json_schema":
                payload["response_format"] = {"type": "json_object"}
            else:
                payload.pop("response_format")
        return response

    def _json_repair_payload(
        self,
        request: ModelGenerationRequest,
        invalid_content: str,
    ) -> dict[str, Any]:
        """Ask the provider to repair syntax without re-running the analysis."""
        return {
            "model": self.provider.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a JSON syntax repair engine. Repair only the JSON syntax "
                        "of the supplied text while preserving its data and meaning. Return "
                        "exactly one complete JSON object with no markdown or commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Repair this malformed JSON and return only the repaired object:\n"
                        + invalid_content
                    ),
                },
            ],
            "temperature": 0.0,
            "max_tokens": request.max_output_tokens,
            "response_format": self._response_format(request),
        }

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.provider.api_key}",
            "Content-Type": "application/json",
            **dict(self.provider.headers),
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.provider.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                async with asyncio.timeout(self.timeout_seconds):
                    return await client.post("/chat/completions", headers=headers, json=payload)
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise ModelGatewayError(
                f"Model provider {self.provider.provider_id} was unavailable within the deadline",
                retryable=True,
            ) from exc

    @staticmethod
    def _response_content(response: httpx.Response) -> str:
        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelGatewayError("Model response did not contain choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise ModelGatewayError("Model response content was empty")
        return content.strip()

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, object]:
        candidate = content.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines:
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end < start:
            raise ModelGatewayError("Model response was not a JSON object")
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            try:
                parsed = repair_json(candidate[start : end + 1], return_objects=True)
            except (TypeError, ValueError, OSError) as repair_exc:
                raise ModelGatewayError("Model response contained invalid JSON") from repair_exc
            if not isinstance(parsed, dict):
                raise ModelGatewayError("Model response contained invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ModelGatewayError("Model response must be a JSON object")
        return parsed
