"""Structured transport is advisory; frozen evidence validation stays mandatory."""
import json
import httpx
import pytest
from app.modules.analysis.domain.model_contract import ModelGenerationRequest
from app.modules.analysis.domain.native_source_contract import NativeSourceDocument
from app.modules.analysis.domain.report_contract import AnalysisReportDocument
from app.modules.analysis.infrastructure.constrained_output import frozen_output_schema
from app.modules.analysis.infrastructure.model_gateway import OpenAICompatibleModelGateway, ModelGatewayError
from app.modules.platform_settings.domain import ModelProviderRuntime
from tests.test_phase9_bounded_source_repair import SequenceGateway, SPEC
from tests.test_phase9_compact_evidence import ALIAS, source_alias_content, report_content, inputs
from tests.test_phase9_native_cutover import doc
from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter


def assert_strict(node):
    if isinstance(node, dict):
        assert 'default' not in node
        if node.get('type') == 'object' and 'properties' in node:
            assert node['additionalProperties'] is False
            assert node['required'] == list(node['properties'])
        for child in node.values():
            assert_strict(child)
    elif isinstance(node, list):
        for child in node:
            assert_strict(child)


@pytest.mark.parametrize('model', [NativeSourceDocument, AnalysisReportDocument])
def test_deep_strict_schema_has_exact_reference_enums_without_changing_model(model):
    before = model.model_json_schema()
    schema = frozen_output_schema(model, [ALIAS])
    assert_strict(schema)
    assert model.model_json_schema() == before
    for name, field in [('NativeSourceFinding', 'document_url'), ('ReportSource', 'url'), ('SourceArticle', 'url')]:
        if name in schema['$defs']:
            assert schema['$defs'][name]['properties'][field]['enum'] == [ALIAS]
    if model is NativeSourceDocument:
        assert schema['properties']['evidence_map']['properties'] == {ALIAS: {'type': 'string'}}


def gateway(handler):
    return OpenAICompatibleModelGateway(ModelProviderRuntime('frozen', 'pinned', 'https://model.test/v1', 'secret'),
                                        transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_structured_payload_is_sent_exactly():
    schema = frozen_output_schema(NativeSourceDocument, [ALIAS])
    captured = []
    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    await gateway(handler).generate_json(ModelGenerationRequest('system', 'frozen input', output_schema=schema))
    assert captured[0]['response_format'] == {'type': 'json_schema', 'json_schema': {
        'name': 'native_analysis', 'strict': True, 'schema': schema}}


@pytest.mark.asyncio
async def test_capability_fallback_is_bounded_and_preserves_provider_and_input():
    captured = []
    async def handler(request):
        captured.append(json.loads(request.content))
        if len(captured) < 3:
            return httpx.Response(400, json={'error': {'message': 'response_format is not supported'}})
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"ok":true}'}}]})
    result = await gateway(handler).generate_json(ModelGenerationRequest('system', 'frozen input', .2, 1000, {'type': 'object'}))
    assert result.model == 'pinned' and len(captured) == 3
    assert [p.get('response_format', {}).get('type') for p in captured] == ['json_schema', 'json_object', None]
    assert all({k: v for k, v in p.items() if k != 'response_format'} == {
        k: v for k, v in captured[0].items() if k != 'response_format'} for p in captured)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,message,retryable', [
    (400, 'invalid schema in response_format', False),
    (401, 'response_format unsupported', False),
    (500, 'response_format unsupported', True),
    (429, 'too many requests', True),
])
async def test_non_capability_errors_do_not_disable_constraints(status, message, retryable):
    captured = []
    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(status, json={'error': {'message': message}})
    with pytest.raises(ModelGatewayError) as exc:
        await gateway(handler).generate_json(ModelGenerationRequest('system', 'input', output_schema={'type': 'object'}))
    assert exc.value.retryable is retryable and len(captured) == 1


@pytest.mark.asyncio
async def test_syntax_repair_keeps_structured_schema():
    captured = []
    async def handler(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': 'not-json' if len(captured) == 1 else '{"ok":true}'}}]})
    await gateway(handler).generate_json(ModelGenerationRequest('system', 'input', output_schema={'type': 'object'}))
    assert len(captured) == 2
    assert captured[0]['response_format'] == captured[1]['response_format']


@pytest.mark.asyncio
async def test_source_and_synthesis_send_frozen_schema_and_restore_references():
    source = SequenceGateway(source_alias_content())
    output = await SingleSourceAnalyzer(source).analyze_source(SPEC, [doc()], 'موضوع')
    assert source.requests[0].output_schema['$defs']['NativeSourceFinding']['properties']['document_url']['enum'] == [ALIAS]
    assert output.findings[0]['document_url'] == doc().url
    synth = SequenceGateway(report_content())
    await RunAggregatorAdapter(pinned_gateway_factory=lambda spec: synth).aggregate(SPEC.to_dict(), inputs(), {})
    assert synth.requests[0].output_schema['$defs']['ReportSource']['properties']['url']['enum'] == [ALIAS]
