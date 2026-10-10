"""Backward-compatible model-provider API.

Use :mod:`provider_queries` or :mod:`provider_commands` in new code.
"""

from app.modules.platform_settings.infrastructure.provider_commands import (
    activate_provider,
    delete_provider_settings,
    save_provider_settings,
)
from app.modules.platform_settings.infrastructure.provider_queries import (
    get_configured_providers,
)

__all__ = [
    "activate_provider",
    "delete_provider_settings",
    "get_configured_providers",
    "save_provider_settings",
]
