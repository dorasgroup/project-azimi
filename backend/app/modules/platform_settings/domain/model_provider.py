"""Runtime configuration required to call a language-model provider."""

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ModelProviderRuntime:
    provider_id: str
    model: str
    base_url: str
    api_key: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict)
