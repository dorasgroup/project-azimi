"""Resolver for SourceAnalysisSpec and RunAnalysisSpec from runtime platform settings and job context."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.modules.analysis.domain.run_analysis_spec import validate_run_spec_secrets
from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec
from app.modules.platform_settings.infrastructure.provider_queries import (
    get_active_model_provider,
)

logger = logging.getLogger("rasad.analysis.spec_resolver")

CANONICAL_DEFAULT_PROMPT_VERSION = "v1"
CANONICAL_DEFAULT_TEMPERATURE = 0.1
CANONICAL_DEFAULT_MAX_TOKENS = 4096
CANONICAL_DEFAULT_LANGUAGE = "fa"


class SourceAnalysisSpecResolver:
    """Resolves RunAnalysisSpec base snapshots and SourceAnalysisSpec instances without secrets."""

    def __init__(
        self,
        default_prompt_version: str = CANONICAL_DEFAULT_PROMPT_VERSION,
        default_temperature: float = CANONICAL_DEFAULT_TEMPERATURE,
        default_max_output_tokens: int = CANONICAL_DEFAULT_MAX_TOKENS,
        default_language: str = CANONICAL_DEFAULT_LANGUAGE,
        require_configured: bool = False,
    ) -> None:
        self._default_prompt_version = default_prompt_version
        self._default_temperature = default_temperature
        self._default_max_output_tokens = default_max_output_tokens
        self._default_language = default_language
        self.require_configured = require_configured

    def resolve_base_spec(
        self,
        custom_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve a frozen base configuration snapshot for a MonitoringRun.

        Contains strictly the 6 invariant parameters:
        1. model_provider
        2. model_name
        3. prompt_version
        4. temperature
        5. max_output_tokens
        6. language_policy

        Ensures zero credentials or secrets are present.
        """
        cfg = dict(custom_config or {})
        validate_run_spec_secrets(cfg)

        # 1. Resolve Provider and Model
        provider_id: str | None = cfg.get("model_provider") or cfg.get("provider_id")
        model_name: str | None = cfg.get("model_name") or cfg.get("model")

        if not provider_id or not model_name:
            try:
                active_runtime = get_active_model_provider()
                provider_id = provider_id or active_runtime.provider_id
                model_name = model_name or active_runtime.model
            except Exception as exc:
                if self.require_configured:
                    raise ValueError("A configured model provider is required before fanout") from exc
                logger.warning(
                    "Could not resolve active model provider from runtime settings: %s; falling back to canonical defaults",
                    exc,
                )
                provider_id = provider_id or "openai"
                model_name = model_name or "gpt-4o-mini"

        # 2. Resolve Hyperparameters
        prompt_version = str(cfg.get("prompt_version") or self._default_prompt_version)
        temperature = float(cfg.get("temperature", self._default_temperature))
        max_output_tokens = int(cfg.get("max_output_tokens", self._default_max_output_tokens))
        language_policy = str(cfg.get("language_policy") or self._default_language)

        base_spec = {
            "model_provider": provider_id,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
            "language_policy": language_policy,
        }
        for key in ("topic", "date_range", "bot_identity", "analysis_requirements", "schema_version"):
            if key in cfg:
                base_spec[key] = str(cfg[key])
        validate_run_spec_secrets(base_spec)
        return base_spec

    def derive_source_spec(
        self,
        base_spec: Mapping[str, Any],
        *,
        topic: str = "",
        date_range: str = "",
        source_title: str = "",
        source_url: str = "",
    ) -> SourceAnalysisSpec:
        """Derive a per-source SourceAnalysisSpec by injecting source context into the frozen base spec."""
        validate_run_spec_secrets(base_spec)
        return SourceAnalysisSpec(
            model_provider=str(base_spec.get("model_provider", "openai")),
            model_name=str(base_spec.get("model_name", "gpt-4o-mini")),
            prompt_version=str(base_spec.get("prompt_version", self._default_prompt_version)),
            temperature=float(base_spec.get("temperature", self._default_temperature)),
            max_output_tokens=int(base_spec.get("max_output_tokens", self._default_max_output_tokens)),
            language_policy=str(base_spec.get("language_policy", self._default_language)),
            topic=topic,
            date_range=date_range,
            source_title=source_title,
            source_url=source_url,
            bot_identity=str(base_spec.get("bot_identity", "")),
            analysis_requirements=str(base_spec.get("analysis_requirements", "")),
            schema_version=str(base_spec.get("schema_version", "1.0")),
        )

    def resolve_spec(
        self,
        *,
        topic: str = "",
        date_range: str = "",
        source_title: str = "",
        source_url: str = "",
        custom_config: Mapping[str, Any] | None = None,
    ) -> SourceAnalysisSpec:
        """Backward-compatible one-step resolver."""
        base_spec = self.resolve_base_spec(custom_config=custom_config)
        return self.derive_source_spec(
            base_spec,
            topic=topic or str((custom_config or {}).get("topic") or ""),
            date_range=date_range or str((custom_config or {}).get("date_range") or ""),
            source_title=source_title or str((custom_config or {}).get("source_title") or ""),
            source_url=source_url or str((custom_config or {}).get("source_url") or ""),
        )
