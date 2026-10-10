"""Frozen short references and length-budget recovery cannot invent evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace
import httpx
import pytest
from app.modules.analysis.domain.model_contract import ModelGenerationRequest
from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec, PermanentAnalysisError, TransientAnalysisError
from app.modules.analysis.infrastructure.model_gateway import OpenAICompatibleModelGateway, ModelOutputTruncatedError, ModelGatewayError
from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
from app.modules.analysis.infrastructure.frozen_evidence_aliases import frozen_evidence_aliases
from app.modules.platform_settings.domain import ModelProviderRuntime
from tests.test_phase9_bounded_source_repair import SequenceGateway
from tests.test_phase9_native_cutover import doc, source_content

SPEC = SourceAnalysisSpec(prompt_version="native-source-v2", model_provider="frozen", model_name="pinned")
ALIAS = "https://evidence.rasad.invalid/1"


def source_alias_content(alias=ALIAS):
    content=source_content()
    content['findings'][0]['document_url']=alias
    content['evidence_map']={alias:'شاهد'}
    return content


def report_content(alias=ALIAS):
    return {'title':'گزارش','executive_summary':{'text':'خلاصه','metrics':[]},
        'timeline':[],'sources_analysis':[],
        'cognitive_analysis':{'sub_sections':[],'key_findings':[]},
        'future_analysis':{'scenarios':[],'key_variables':[]},'conclusion':'جمع‌بندی',
        'sources':[{'title':'شاهد','source_name':'منبع','url':alias}]}


def inputs(url='https://example.invalid/news/1'):
    return [{'source_id':'source','source_analysis_result_id':'result','input_digest':'a'*64,
        'summary':'خلاصه','findings':[],'evidence_map':{url:'شاهد'},
        'raw_result':{'source_title':'منبع','document_headers':[]}}]


def test_aliases_are_exact_deduplicated_and_do_not_collide_with_input():
    urls=[ALIAS,'https://example.invalid/news/1',ALIAS]
    aliases=frozen_evidence_aliases(urls)
    assert len(aliases)==2 and set(aliases.values())==set(urls)
    assert set(aliases).isdisjoint(urls)


@pytest.mark.asyncio
async def test_truncated_even_valid_json_never_enters_syntax_repair():
    calls=[]
    async def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'{"ok":true}'}}]})
    gateway=OpenAICompatibleModelGateway(ModelProviderRuntime('test','model','https://model.test/v1','secret'),transport=httpx.MockTransport(handler))
    with pytest.raises(ModelOutputTruncatedError):
        await gateway.generate_json(ModelGenerationRequest('system','user'))
    assert len(calls)==1


@pytest.mark.asyncio
async def test_short_frozen_source_reference_restores_full_long_url():
    item=doc()
    item.url=item.canonical_url='https://example.invalid/news/'+('%D8%A7'*600)
    gateway=SequenceGateway(source_alias_content())
    result=await SingleSourceAnalyzer(gateway).analyze_source(SPEC,[item],'موضوع')
    assert item.url not in gateway.requests[0].user_prompt and ALIAS in gateway.requests[0].user_prompt
    assert result.findings[0]['document_url']==item.url
    assert set(result.evidence_map)=={item.url}
    assert result.raw_result['document_headers'][0]['url']==item.url


@pytest.mark.asyncio
@pytest.mark.parametrize('alias',['https://evidence.rasad.invalid/99','https://evidence.rasad.invalid/1?invented=yes'])
async def test_unknown_source_reference_fails_without_regeneration(alias):
    gateway=SequenceGateway(source_alias_content(alias))
    with pytest.raises(PermanentAnalysisError,match='outside frozen'):
        await SingleSourceAnalyzer(gateway).analyze_source(SPEC,[doc()],'موضوع')
    assert len(gateway.requests)==1


@pytest.mark.asyncio
async def test_source_truncation_regenerates_once_with_same_budget():
    gateway=SequenceGateway(ModelOutputTruncatedError('truncated'),source_alias_content())
    result=await SingleSourceAnalyzer(gateway).analyze_source(SPEC,[doc()],'موضوع')
    assert len(gateway.requests)==2 and result.raw_result['schema_repair_attempts']==1
    assert gateway.requests[0].max_output_tokens==gateway.requests[1].max_output_tokens
    assert gateway.requests[1].user_prompt.startswith(gateway.requests[0].user_prompt)


@pytest.mark.asyncio
async def test_repeated_truncation_is_terminal_not_success():
    gateway=SequenceGateway(ModelOutputTruncatedError('first'),ModelOutputTruncatedError('second'))
    with pytest.raises(PermanentAnalysisError,match='bounded repair'):
        await SingleSourceAnalyzer(gateway).analyze_source(SPEC,[doc()],'موضوع')
    assert len(gateway.requests)==2


@pytest.mark.asyncio
async def test_synthesis_resolves_frozen_reference_and_keeps_long_input_anchor():
    url='https://example.invalid/news/'+('%D8%A7'*100)
    gateway=SequenceGateway(report_content())
    result=await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),inputs(url),{'analysis_successful':1})
    assert ALIAS in gateway.requests[0].user_prompt and url not in gateway.requests[0].user_prompt
    assert result['raw_result']['report_snapshot']['document']['sources'][0]['url']==url
    assert result['raw_result']['schema_repair_attempts']==0


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',[{},ModelOutputTruncatedError('truncated')])
async def test_synthesis_has_one_bounded_regeneration(invalid):
    gateway=SequenceGateway(invalid,report_content())
    result=await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),inputs(),{'analysis_successful':1})
    assert len(gateway.requests)==2 and result['raw_result']['schema_repair_attempts']==1
    assert gateway.requests[1].system_prompt==gateway.requests[0].system_prompt
    assert gateway.requests[1].max_output_tokens==gateway.requests[0].max_output_tokens


@pytest.mark.asyncio
async def test_synthesis_unknown_reference_is_never_accepted():
    gateway=SequenceGateway(report_content('https://evidence.rasad.invalid/99'))
    with pytest.raises(PermanentAnalysisError,match='outside frozen'):
        await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),inputs(),{'analysis_successful':1})
    assert len(gateway.requests)==1


@pytest.mark.asyncio
async def test_provider_outage_does_not_switch_or_enter_schema_repair():
    gateway=SequenceGateway(ModelGatewayError('private',retryable=True))
    with pytest.raises(TransientAnalysisError):
        await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(SPEC.to_dict(),inputs(),{'analysis_successful':1})
    assert len(gateway.requests)==1
