"""Project-owned model-provider settings store.

This module is the single write path for the native analysis engine.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.modules.platform_settings.infrastructure.provider_runtime import (
    ModelProviderConfigurationError,
    get_local_provider_summaries,
    resolve_provider_runtime,
)
from app.modules.platform_settings.infrastructure.runtime_settings import (
    _read_runtime_settings,
    _write_runtime_settings,
)


SUPPORTED_PROVIDERS: dict[str, str] = {
    "openai": "OpenAI",
    "openrouter": "OpenRouter",
    "deepseek": "DeepSeek",
    "ollama": "Ollama",
    "openai_api_compatible": "OpenAI-API-compatible",
    "gapgpt": "گپ جی پی تی (GapGPT)",
    "avalai": "اول ای آی (AvalAI)",
    "gemini": "Google Gemini",
}


def _records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("model_providers", [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _summary(provider_id: str) -> dict[str, object]:
    for item in get_local_provider_summaries():
        if item["id"] == provider_id:
            return item
    raise ModelProviderConfigurationError(f"Provider {provider_id} was not saved")


def _validate_record(record: dict[str, Any]) -> None:
    resolve_provider_runtime(
        record,
        worker_url=settings.ai_cloudflare_worker_url,
        worker_secret=settings.ai_cloudflare_worker_secret,
    )


async def save_provider_settings(
    provider_id: str,
    api_key: str | None,
    model: str | None,
    embedding_model: str | None,
) -> dict[str, object]:
    provider_id = provider_id.strip().lower()
    if provider_id not in SUPPORTED_PROVIDERS:
        raise ModelProviderConfigurationError(
            f"Provider {provider_id} is not supported by the native analysis engine"
        )

    payload = _read_runtime_settings()
    records = _records(payload)
    existing = next((item for item in records if item.get("id") == provider_id), None)
    candidate = dict(existing or {"id": provider_id, "is_active": False})
    if api_key is not None and api_key.strip():
        candidate["api_key"] = api_key.strip()
    if model is not None and model.strip():
        candidate["active_model"] = model.strip()
    if embedding_model is not None:
        candidate["active_embedding_model"] = embedding_model.strip()

    _validate_record(candidate)
    records = [item for item in records if item.get("id") != provider_id]
    records.append(candidate)
    payload["model_providers"] = records
    _write_runtime_settings(payload)
    return _summary(provider_id)


async def activate_provider(provider_id: str) -> list[dict[str, object]]:
    provider_id = provider_id.strip().lower()
    payload = _read_runtime_settings()
    records = _records(payload)
    candidate = next((item for item in records if item.get("id") == provider_id), None)
    if candidate is None:
        raise ModelProviderConfigurationError(f"Provider {provider_id} is not configured")
    _validate_record(candidate)
    for item in records:
        item["is_active"] = item.get("id") == provider_id
    payload["model_providers"] = records
    _write_runtime_settings(payload)
    return get_local_provider_summaries()


async def delete_provider_settings(provider_id: str) -> list[dict[str, object]]:
    provider_id = provider_id.strip().lower()
    payload = _read_runtime_settings()
    records = _records(payload)
    existing = next((item for item in records if item.get("id") == provider_id), None)
    if existing is not None and existing.get("is_active") is True:
        raise ModelProviderConfigurationError(
            "Activate another provider before deleting the active provider"
        )
    payload["model_providers"] = [
        item for item in records if item.get("id") != provider_id
    ]
    _write_runtime_settings(payload)
    return get_local_provider_summaries()
