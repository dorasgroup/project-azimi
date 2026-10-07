"""A schema failure gets one same-input regeneration, never weaker evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec, PermanentAnalysisError, TransientAnalysisError
from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
from app.modules.analysis.infrastructure.model_gateway import ModelGatewayError
from tests.test_phase9_native_cutover import doc, source_content


class SequenceGateway:
    def __init__(self, *outputs):
        self.outputs, self.requests = outputs, []

    async def generate_json(self, request):
        index = len(self.requests)
        self.requests.append(request)
        output = self.outputs[index]
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(content=deepcopy(output))


SPEC = SourceAnalysisSpec(prompt_version="native-source-v2", model_provider="frozen", model_name="pinned")


@pytest.mark.asyncio
async def test_missing_section_recovers_once_without_changing_inputs_or_provider():
    invalid = source_content()
    del invalid["future_analysis"]
    invalid["summary"] = "INVALID_RESPONSE_MUST_NOT_BE_ECHOED"
    gateway = SequenceGateway(invalid, source_content())
    snapshots = []
    def factory(snapshot):
        snapshots.append(snapshot)
        return gateway
    result = await SingleSourceAnalyzer(None, pinned_gateway_factory=factory).analyze_source(SPEC, [doc()], "مذاکرات")
    assert len(gateway.requests) == 2 and len(snapshots) == 1
    first, repair = gateway.requests
    assert repair.user_prompt.startswith(first.user_prompt)
    assert repair.system_prompt == first.system_prompt
    assert repair.max_output_tokens == first.max_output_tokens and repair.temperature == first.temperature
    assert "JSON Schema" in first.system_prompt and "future_analysis" in repair.user_prompt
    assert "INVALID_RESPONSE_MUST_NOT_BE_ECHOED" not in repair.user_prompt
    assert result.raw_result["schema_repair_attempts"] == 1
    assert result.raw_result["future_analysis"] == {"scenarios": [], "key_variables": []}


@pytest.mark.asyncio
async def test_repeated_invalid_is_permanent_after_exactly_two_generations():
    gateway = SequenceGateway({}, {})
    with pytest.raises(PermanentAnalysisError, match="bounded repair"):
        await SingleSourceAnalyzer(gateway).analyze_source(SPEC, [doc()], "مذاکرات")
    assert len(gateway.requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_outside_frozen_evidence_is_never_accepted_or_retried(repair):
    invalid = source_content()
    invalid["findings"][0]["document_url"] = "https://invented.invalid/"
    gateway = SequenceGateway({}, invalid) if repair else SequenceGateway(invalid)
    with pytest.raises(PermanentAnalysisError, match="outside frozen"):
        await SingleSourceAnalyzer(gateway).analyze_source(SPEC, [doc()], "مذاکرات")
    assert len(gateway.requests) == (2 if repair else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("retryable", [False, True])
async def test_provider_errors_are_not_schema_regenerations(retryable):
    gateway = SequenceGateway(ModelGatewayError("private failure", retryable=retryable))
    with pytest.raises(TransientAnalysisError if retryable else PermanentAnalysisError, match="provider failure"):
        await SingleSourceAnalyzer(gateway).analyze_source(SPEC, [doc()], "مذاکرات")
    assert len(gateway.requests) == 1


@pytest.mark.asyncio
async def test_valid_schema_has_no_extra_provider_call():
    gateway = SequenceGateway(source_content())
    result = await SingleSourceAnalyzer(gateway).analyze_source(SPEC, [doc()], "مذاکرات")
    assert len(gateway.requests) == 1 and result.raw_result["schema_repair_attempts"] == 0
