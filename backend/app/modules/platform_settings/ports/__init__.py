"""Platform-settings contracts."""

from typing import Protocol


class ProviderSettingsGateway(Protocol):
    """Reads project-owned model-provider configuration."""

    async def list_configured(self) -> list[dict[str, object]]: ...
