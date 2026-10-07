"""Native two-stage cognitive-analysis workflow."""

from __future__ import annotations

import json
import re
from typing import Mapping

from pydantic import ValidationError as PydanticValidationError

from app.modules.analysis.domain import (
    AnalysisOutcome,
    AnalysisReportDocument,
    AnalysisRequest,
    ModelGenerationRequest,
)
from app.modules.analysis.infrastructure.model_gateway import ModelGatewayError
from app.modules.analysis.infrastructure.report_mapping import report_payload
from app.modules.analysis.ports import ModelGateway
from app.modules.analysis.prompts.v1 import (
    REPORT_SYNTHESIS_SYSTEM_PROMPT,
    REPORT_SYNTHESIS_USER_TEMPLATE,
    SEMANTIC_FILTER_SYSTEM_PROMPT,
    SEMANTIC_FILTER_USER_TEMPLATE,
)


class NativeAnalysisError(RuntimeError):
    pass


PROMPT_VARIABLE_RE = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def _text(inputs: Mapping[str, object], key: str) -> str:
    value = inputs.get(key)
    if value is None:
        raise NativeAnalysisError(f"Analysis input is missing required field: {key}")
    return str(value)


def _render(template: str, values: Mapping[str, str]) -> str:
    required_variables = set(PROMPT_VARIABLE_RE.findall(template))
    missing_variables = sorted(required_variables.difference(values))
    if missing_variables:
        raise NativeAnalysisError(
            "Analysis prompt contains unresolved variables: "
            + ", ".join(missing_variables)
        )
    return PROMPT_VARIABLE_RE.sub(lambda match: values[match.group(1)], template)


class NativeAnalysisGateway:
    """Run semantic filtering and report synthesis inside RASAD."""

    def __init__(self, model: ModelGateway) -> None:
        self._model = model

    async def analyze(self, request: AnalysisRequest) -> AnalysisOutcome:
        inputs = request.inputs
        semantic_user = _render(
            SEMANTIC_FILTER_USER_TEMPLATE,
            {
                "topic": _text(inputs, "topic"),
                "date_range": _text(inputs, "date_range"),
                "source_urls": _text(inputs, "source_urls"),
                "source_documents": _text(inputs, "source_documents"),
            },
        )
        try:
            semantic = await self._model.generate_json(
                ModelGenerationRequest(
                    system_prompt=SEMANTIC_FILTER_SYSTEM_PROMPT,
                    user_prompt=semantic_user,
                    temperature=0.0,
                    max_output_tokens=4096,
                )
            )
            semantic_content = self._normalize_semantic_evidence(semantic.content)
            self._validate_semantic_evidence(semantic_content)
            synthesis_user = _render(
                REPORT_SYNTHESIS_USER_TEMPLATE,
                {
                    "topic": _text(inputs, "topic"),
                    "date_range": _text(inputs, "date_range"),
                    "bot_identity": _text(inputs, "bot_identity"),
                    "analysis_requirements": _text(inputs, "analysis_requirements"),
                    "output_format": _text(inputs, "output_format"),
                    "semantic_evidence": json.dumps(
                        semantic_content, ensure_ascii=False, default=str
                    ),
                },
            )
            generated = await self._model.generate_json(
                ModelGenerationRequest(
                    system_prompt=REPORT_SYNTHESIS_SYSTEM_PROMPT,
                    user_prompt=synthesis_user,
                    temperature=0.1,
                    max_output_tokens=8192,
                )
            )
            report = AnalysisReportDocument.model_validate(generated.content)
        except (ModelGatewayError, PydanticValidationError, TypeError, ValueError) as exc:
            raise NativeAnalysisError(f"Native analysis failed: {exc}") from exc
        return AnalysisOutcome(report=report_payload(report))

    @staticmethod
    def _normalize_semantic_evidence(
        content: Mapping[str, object],
    ) -> dict[str, object]:
        normalized = dict(content)
        normalized.setdefault("excluded_documents", [])
        normalized.setdefault("evidence_map", {})
        normalized.setdefault("coverage_notes", "")
        return normalized

    @staticmethod
    def _validate_semantic_evidence(content: Mapping[str, object]) -> None:
        required = {
            "selected_documents",
            "excluded_documents",
            "evidence_map",
            "coverage_notes",
        }
        missing = required.difference(content)
        if missing:
            raise NativeAnalysisError(
                "Semantic filter response is missing fields: " + ", ".join(sorted(missing))
            )
        if not isinstance(content["selected_documents"], list):
            raise NativeAnalysisError("Semantic selected_documents must be an array")
