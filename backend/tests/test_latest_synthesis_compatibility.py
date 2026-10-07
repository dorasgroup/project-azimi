"""The full 30-source run exposed a decoder/schema compatibility failure."""
from copy import deepcopy
import json
import httpx
import pytest
from app.modules.analysis.infrastructure.constrained_output import frozen_output_schema
from app.modules.analysis.domain.report_contract import AnalysisReportDocument
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
from app.modules.analysis.infrastructure.model_gateway import OpenAICompatibleModelGateway
from app.modules.platform_settings.domain import ModelProviderRuntime
from tests.test_phase9_compact_evidence import inputs, report_content, SPEC, ALIAS
from tests.test_phase9_bounded_source_repair import SequenceGateway

def test_exact_url_enums_need_no_redundant_uri_decoder_constraint():
    schema=frozen_output_schema(AnalysisReportDocument,[ALIAS])
    for name in ('ReportSource','SourceArticle'):
        assert schema['$defs'][name]['properties']['url']=={'type':'string','enum':[ALIAS]}
        assert schema['$defs'][name]['additionalProperties'] is False

@pytest.mark.asyncio
async def test_decoder_compatibility_still_requires_typed_frozen_evidence():
    async def handle(request):
        payload=json.loads(request.content)
        defs=payload['response_format']['json_schema']['schema']['$defs']
        if any('format' in defs[n]['properties']['url'] for n in ('ReportSource','SourceArticle')):
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':''}}]})
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(report_content())}}]})
    gateway=OpenAICompatibleModelGateway(ModelProviderRuntime('test','pinned','https://model.test/v1','secret'),transport=httpx.MockTransport(handle))
    result=await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),inputs(),{'expected_sources':30})
    assert result['raw_result']['report_snapshot']['document']['sources'][0]['url']=='https://example.invalid/news/1'
    assert result['raw_result']['schema_repair_attempts']==0

@pytest.mark.asyncio
async def test_empty_sources_do_not_multiply_model_compaction_calls():
    accepted=inputs()
    empty={'source_id':'empty','source_analysis_result_id':'empty-result','summary':'No documents','findings':[],'evidence_map':{},'raw_result':{'document_headers':[]}}
    gateway=SequenceGateway(report_content())
    result=await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),accepted+[deepcopy(empty) for _ in range(29)],{'expected_sources':30,'analysis_successful':30})
    assert len(gateway.requests)==1
    assert result['coverage_metadata']['expected_sources']==30
    assert len(result['provenance_references'])==30
