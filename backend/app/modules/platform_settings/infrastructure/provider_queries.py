"""Read-only model-provider gateways exposed to composition and analysis."""

from typing import Any

from app.modules.platform_settings.infrastructure.provider_runtime import (
    get_active_model_provider,
    get_local_provider_summaries,
)


async def get_configured_providers() -> list[dict[str, Any]]:
    """Return model-provider settings from project-owned storage."""

    return get_local_provider_summaries()


__all__ = [
    "get_active_model_provider",
    "get_configured_providers",
    "get_local_provider_summaries",
]
