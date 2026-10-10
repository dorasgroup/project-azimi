"""Provider-neutral language-model request and result contracts."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ModelGenerationRequest:
    system_prompt: str
    user_prompt: str
    temperature: float = 0.1
    max_output_tokens: int = 8192
    output_schema: Mapping[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ModelGenerationResult:
    content: Mapping[str, object]
    provider_id: str
    model: str
