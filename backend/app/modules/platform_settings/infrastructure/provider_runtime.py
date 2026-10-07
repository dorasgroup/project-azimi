"""Resolve model-provider settings from project-owned storage."""

from __future__ import annotations

import base64
from typing import Any, Mapping

from app.core.config import settings
from app.modules.platform_settings.domain import ModelProviderRuntime
from app.modules.platform_settings.infrastructure.runtime_settings import (
    _read_runtime_settings,
    mask_secret,
)

PROVIDER_NAMES = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openai_api_compatible": "OpenAI-API-compatible",
    "gapgpt": "گپ جی پی تی (GapGPT)",
    "avalai": "اول ای آی (AvalAI)",
    "gemini": "Google Gemini",
}


class ModelProviderConfigurationError(RuntimeError):
    pass


DIRECT_OPENAI_COMPATIBLE_ENDPOINTS = {
    "gapgpt": "https://api.gapgpt.app/v1",
    "avalai": "https://api.avalai.ir/v1",
    "deepseek": "https://api.deepseek.com/v1",
}

WORKER_PROVIDER_ROUTES = {
    "openai": "openai",
    "openrouter": "openrouter",
    "gemini": "google",
}


def _base64url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _required_text(record: Mapping[str, Any], key: str) -> str:
    value = str(record.get(key) or "").strip()
    if not value:
        raise ModelProviderConfigurationError(
            f"Active model provider is missing required field: {key}"
        )
    return value


def resolve_provider_runtime(
    record: Mapping[str, Any],
    *,
    worker_url: str = "",
    worker_secret: str = "",
) -> ModelProviderRuntime:
    """Turn a local provider record into an OpenAI-compatible runtime target."""

    provider_id = _required_text(record, "id").lower()
    model = _required_text(record, "active_model")
    configured_value = _required_text(record, "api_key")

    if provider_id in WORKER_PROVIDER_ROUTES:
        worker_url = worker_url.strip().rstrip("/")
        worker_secret = worker_secret.strip()
        if not worker_url or not worker_secret:
            raise ModelProviderConfigurationError(
                f"Cloudflare AI gateway is required for provider {provider_id}"
            )
        route = WORKER_PROVIDER_ROUTES[provider_id]
        token = f"teb1.{_base64url(worker_secret)}.{_base64url(configured_value)}"
        return ModelProviderRuntime(
            provider_id=provider_id,
            model=model,
            base_url=f"{worker_url}/{route}/v1",
            api_key=token,
        )

    direct_endpoint = DIRECT_OPENAI_COMPATIBLE_ENDPOINTS.get(provider_id)
    if direct_endpoint:
        return ModelProviderRuntime(
            provider_id=provider_id,
            model=model,
            base_url=direct_endpoint,
            api_key=configured_value,
        )

    if provider_id == "openai_api_compatible":
        endpoint, separator, api_key = configured_value.partition("|")
        if not separator or not endpoint.strip() or not api_key.strip():
            raise ModelProviderConfigurationError(
                "OpenAI-compatible provider must be stored as endpoint|api_key"
            )
        return ModelProviderRuntime(
            provider_id=provider_id,
            model=model,
            base_url=endpoint.strip().rstrip("/"),
            api_key=api_key.strip(),
        )

    if provider_id == "ollama":
        return ModelProviderRuntime(
            provider_id=provider_id,
            model=model,
            base_url=configured_value.rstrip("/") + "/v1",
            api_key="ollama-local",
        )

    raise ModelProviderConfigurationError(
        f"Provider {provider_id} does not expose an OpenAI-compatible chat endpoint"
    )


def _local_provider_records() -> list[dict[str, Any]]:
    records = _read_runtime_settings().get("model_providers", [])
    if not isinstance(records, list):
        return []
    return [dict(item) for item in records if isinstance(item, dict)]


def get_active_model_provider() -> ModelProviderRuntime:
    active = [item for item in _local_provider_records() if item.get("is_active") is True]
    if len(active) != 1:
        raise ModelProviderConfigurationError(
            f"Exactly one active model provider is required; found {len(active)}"
        )
    return resolve_provider_runtime(
        active[0],
        worker_url=settings.ai_cloudflare_worker_url,
        worker_secret=settings.ai_cloudflare_worker_secret,
    )


def get_pinned_model_provider(provider_id: str, model: str) -> ModelProviderRuntime:
    """Resolve only the frozen provider identity, with execution-time credentials.

    A later active-provider/model switch must never retarget an existing run.
    Deleted or ambiguous records fail closed, even if another provider is active.
    """
    records = [item for item in _local_provider_records() if item.get("id") == provider_id]
    if len(records) != 1 or not model.strip():
        raise ModelProviderConfigurationError("Frozen model provider is unavailable")
    record = {**records[0], "active_model": model}
    return resolve_provider_runtime(
        record, worker_url=settings.ai_cloudflare_worker_url,
        worker_secret=settings.ai_cloudflare_worker_secret,
    )


def get_local_provider_summaries() -> list[dict[str, object]]:
    """Return UI-safe provider metadata from RASAD storage only."""

    result: list[dict[str, object]] = []
    for item in _local_provider_records():
        api_key = str(item.get("api_key") or "")
        provider_id = str(item.get("id") or "")
        result.append(
            {
                "id": provider_id,
                "name": PROVIDER_NAMES.get(provider_id, provider_id),
                "api_key_masked": mask_secret(api_key),
                "has_api_key": bool(api_key),
                "active_model": str(item.get("active_model") or ""),
                "active_embedding_model": str(item.get("active_embedding_model") or ""),
                "is_active": item.get("is_active") is True,
                "status": "configured" if api_key else "not_configured",
            }
        )
    return result
