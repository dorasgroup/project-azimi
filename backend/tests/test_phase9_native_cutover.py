"""Native cutover composition, immutable config and cognitive output acceptance."""

import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.db.session import SessionFactory, engine
from app.models import User, Source, BotIdentity, MonitoringJob, SourceType
from app.models.source_task import SourceTask
from app.models.source_analysis import OrmSourceAnalysis, OrmRunAnalysisSpec
from app.models.integration_outbox import IntegrationOutbox
from app.modules.documents.infrastructure import SqlAlchemyDocumentRepository
from app.modules.monitoring.infrastructure.repository import SqlAlchemyMonitoringJobRepository
from app.modules.monitoring.infrastructure.pipeline_adapters import SqlAlchemyPipelinePersistenceAdapter
from app.modules.monitoring.infrastructure.pipeline_steps import MonitoringPipelineSteps
from app.modules.analysis.infrastructure.handoff_adapter import AnalysisHandoffAdapter
from app.modules.analysis.infrastructure.spec_resolver import SourceAnalysisSpecResolver
from app.modules.analysis.infrastructure.single_source_analyzer import SingleSourceAnalyzer
from app.modules.analysis.infrastructure.run_aggregator_adapter import RunAggregatorAdapter
from app.modules.analysis.domain.source_analysis import SourceAnalysisSpec, PermanentAnalysisError
from app.modules.platform_settings.infrastructure import provider_runtime


async def fixture_job():
    await engine.dispose()
    async with SessionFactory() as session, session.begin():
        user = User(full_name="Synthetic Phase9", username=uuid4().hex,
                    email=f"{uuid4().hex}@example.invalid", password_hash="synthetic", is_active=False)
        session.add(user)
        await session.flush()
        bot = BotIdentity(title="Pinned identity", role_definition="Pinned cognitive policy", created_by=user.id)
        source = Source(title="Pinned source", base_url=f"https://{uuid4().hex}.example.invalid",
                        type=SourceType.NEWS_SITE, created_by=user.id)
        session.add_all([bot, source])
        await session.flush()
        job = MonitoringJob(topic="مذاکرات", description="الزام ثابت",
                            date_from_jalali="1405/07/01", date_to_jalali="1405/07/08",
                            date_from_gregorian=date(2026,9,23), date_to_gregorian=date(2026,9,30),
                            requested_by=user.id, bot_identity_id=bot.id, sources=[source])
        session.add(job)
        await session.flush()
        return job.id, bot.id


def pipeline():
    from app.bootstrap.distributed_analysis import provide_distributed_analysis_freezer
    persistence = SqlAlchemyPipelinePersistenceAdapter(
        SessionFactory, SqlAlchemyMonitoringJobRepository, SqlAlchemyDocumentRepository
    )
    steps = MonitoringPipelineSteps.__new__(MonitoringPipelineSteps)
    steps._session_factory = SessionFactory
    steps._run_spec_freezer = provide_distributed_analysis_freezer(SessionFactory)
    return persistence, steps


@pytest.mark.asyncio
async def test_real_distributed_start_freezes_before_dispatch_and_identity_only_handoff():
    job_id, bot_id = await fixture_job()
    persistence, steps = pipeline()
    runtime = SimpleNamespace(provider_id="gapgpt", model="pinned-model")
    with patch("app.modules.monitoring.infrastructure.pipeline_adapters.settings.monitoring_distributed_fanout_enabled", True), \
         patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider", return_value=runtime):
        _, context, resume = await persistence.load_and_start(job_id)
        assert not resume
        async with SessionFactory() as session:
            assert not list((await session.scalars(select(SourceTask).where(SourceTask.run_id==context.run_id))).all())
        result = await steps.process_sources(context)
        assert result.terminal_result["dispatched_tasks"] == 1
        async with SessionFactory() as session:
            task = await session.scalar(select(SourceTask).where(SourceTask.run_id==context.run_id))
            assert task.execution_spec["topic"].startswith("مذاکرات")
            assert task.execution_spec["base_url"].endswith(".example.invalid")
            frozen = await session.scalar(select(OrmRunAnalysisSpec).where(OrmRunAnalysisSpec.run_id==context.run_id))
            original = dict(frozen.spec)
            assert original["prompt_version"] == "native-source-v2"
            assert original["model_name"] == "pinned-model"
            assert "Pinned cognitive policy" in original["bot_identity"]
            bot = await session.get(BotIdentity, bot_id)
            bot.role_definition = "NEW MUST NOT LEAK"
            task_id = task.id
            await session.commit()
        freezer = AnalysisHandoffAdapter(SessionFactory, freeze_job_context=True,
            spec_resolver=SourceAnalysisSpecResolver(require_configured=True))
        runtime.model = "different-active-model"
        refrozen = await freezer.ensure_run_analysis_spec(context.run_id)
        assert refrozen.spec == original
        await freezer.ensure_and_dispatch(task_id)
        async with SessionFactory() as session:
            analysis = await session.scalar(select(OrmSourceAnalysis).where(OrmSourceAnalysis.source_task_id==task_id))
            assert analysis.analysis_spec["topic"] == "مذاکرات"
            assert analysis.analysis_spec["date_range"] == "1405/07/01 تا 1405/07/08"
            assert analysis.analysis_spec["source_title"] == "Pinned source"
            assert "NEW MUST NOT LEAK" not in analysis.analysis_spec["bot_identity"]
            assert analysis.analysis_spec["model_name"] == "pinned-model"
    await engine.dispose()


@pytest.mark.asyncio
async def test_provider_freeze_failure_does_not_create_or_dispatch_source_work():
    job_id, _ = await fixture_job()
    persistence, steps = pipeline()
    with patch("app.modules.monitoring.infrastructure.pipeline_adapters.settings.monitoring_distributed_fanout_enabled", True), \
         patch("app.modules.analysis.infrastructure.spec_resolver.get_active_model_provider", side_effect=RuntimeError("private-marker")):
        _, context, _ = await persistence.load_and_start(job_id)
        with pytest.raises(ValueError, match="configured model provider"):
            await steps.process_sources(context)
        async with SessionFactory() as session:
            assert not list((await session.scalars(select(SourceTask).where(SourceTask.run_id==context.run_id))).all())
    await engine.dispose()


def test_pinned_provider_uses_inactive_record_and_frozen_model_not_new_active(monkeypatch):
    monkeypatch.setattr(provider_runtime, "_local_provider_records", lambda: [
        {"id":"gapgpt", "active_model":"new-model", "api_key":"private-marker", "is_active":False},
        {"id":"avalai", "active_model":"other", "api_key":"other-marker", "is_active":True},
    ])
    resolved = provider_runtime.get_pinned_model_provider("gapgpt", "original-model")
    assert (resolved.provider_id, resolved.model) == ("gapgpt", "original-model")
    with pytest.raises(provider_runtime.ModelProviderConfigurationError):
        provider_runtime.get_pinned_model_provider("missing", "original-model")


def doc():
    return SimpleNamespace(source_id=uuid4(), url="https://example.invalid/news/1", title="سند",
        clean_text="خبر مذاکرات", published_at=None, language="fa", canonical_url="https://example.invalid/news/1",
        content_hash="a"*64, matched_score=1)


class Gateway:
    def __init__(self, content):
        self.content, self.requests = content, []
    async def generate_json(self, request):
        self.requests.append(request)
        return SimpleNamespace(content=self.content)


def source_content():
    return {"summary":"تحلیل شناختی مستند", "findings":[{"title":"یافته", "explanation":"شرح",
        "evidence":"خبر مذاکرات", "document_url":"https://example.invalid/news/1"}],
        "evidence_map":{"https://example.invalid/news/1":"خبر مذاکرات"},
        "cognitive_analysis":{"sub_sections":[{"title":"روایت", "content":"شرح روایت"}], "key_findings":["یافته"]},
        "future_analysis":{"scenarios":[], "key_variables":[]}, "coverage_notes":[]}


@pytest.mark.asyncio
async def test_native_source_uses_frozen_identity_and_typed_evidence():
    gateway = Gateway(source_content())
    spec = SourceAnalysisSpec(prompt_version="native-source-v2", model_provider="gapgpt", model_name="pinned",
                              bot_identity="ثابت", analysis_requirements="الزام")
    captured = []
    def factory(snapshot):
        captured.append(snapshot)
        return gateway
    result = await SingleSourceAnalyzer(lambda: pytest.fail("active provider must not be used"),
        pinned_gateway_factory=factory).analyze_source(spec, [doc()], "مذاکرات")
    assert captured[0]["model_name"] == "pinned"
    assert "ثابت" in gateway.requests[0].user_prompt
    assert "cognitive_analysis" in result.raw_result


@pytest.mark.asyncio
async def test_native_source_dates_are_jalali_and_prompt_identity_is_bounded():
    from datetime import datetime, UTC
    item = doc()
    item.published_at = datetime(2026, 9, 23, tzinfo=UTC)
    gateway = Gateway(source_content())
    result = await SingleSourceAnalyzer(gateway).analyze_source(
        SourceAnalysisSpec(prompt_version="native-source-v2", bot_identity="x"*100000), [item], "مذاکرات")
    assert result.raw_result["document_headers"][0]["published_at"] == "1405/07/01"
    assert len(gateway.requests[0].user_prompt) < 15000


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["schema", "evidence"])
async def test_bad_source_contract_is_not_success(bad):
    content = source_content()
    if bad == "schema":
        content["summary"] = {"wrong":"type"}
    else:
        content["findings"][0]["document_url"] = "https://invented.invalid/"
    with pytest.raises(PermanentAnalysisError):
        await SingleSourceAnalyzer(Gateway(content)).analyze_source(
            SourceAnalysisSpec(prompt_version="native-source-v2"), [doc()], "مذاکرات")


@pytest.mark.asyncio
async def test_empty_source_digest_is_deterministic_without_model_calls():
    analyzer = SingleSourceAnalyzer(lambda: pytest.fail("no-doc source must not call model"))
    a = await analyzer.analyze_source(SourceAnalysisSpec(), [], "مذاکرات")
    b = await analyzer.analyze_source(SourceAnalysisSpec(), [], "مذاکرات")
    assert a.input_digest == b.input_digest


@pytest.mark.asyncio
async def test_semantically_excluded_documents_are_not_report_evidence():
    related, excluded = doc(), doc()
    excluded.url = excluded.canonical_url = "https://example.invalid/news/unrelated"
    gateway = Gateway(source_content())
    result = await SingleSourceAnalyzer(gateway).analyze_source(
        SourceAnalysisSpec(prompt_version="native-source-v2"), [related, excluded], "مذاکرات")
    assert result.raw_result["documents_analyzed"] == 2
    assert result.raw_result["semantic_selected_documents"] == 1
    assert [h["url"] for h in result.raw_result["document_headers"]] == [related.url]
    assert excluded.url not in result.evidence_map


@pytest.mark.asyncio
async def test_no_relevant_evidence_fails_synthesis_without_model_call():
    content = source_content()
    content["findings"], content["evidence_map"] = [], {}
    source = await SingleSourceAnalyzer(Gateway(content)).analyze_source(
        SourceAnalysisSpec(prompt_version="native-source-v2"), [doc()], "مذاکرات")
    assert source.raw_result["semantic_selected_documents"] == 0
    assert source.raw_result["document_headers"] == []
    with pytest.raises(PermanentAnalysisError, match="relevant source evidence"):
        await RunAggregatorAdapter(pinned_gateway_factory=lambda spec: pytest.fail("no model call")).aggregate(
            {"prompt_version":"native-source-v2"}, [{"source_id":str(uuid4()),
            "source_analysis_result_id":str(uuid4()), "summary":source.summary,
            "findings":source.findings, "evidence_map":source.evidence_map, "raw_result":source.raw_result}],
            {"expected_sources":1})


@pytest.mark.asyncio
async def test_zero_input_aggregation_has_typed_no_evidence_failure():
    from app.modules.analysis.domain.source_analysis import NoRelevantEvidenceError
    with pytest.raises(NoRelevantEvidenceError):
        await RunAggregatorAdapter(pinned_gateway_factory=lambda spec: pytest.fail('no model call')).aggregate(
            {'prompt_version':'native-source-v2'}, [], {'expected_sources':50})


@pytest.mark.asyncio
async def test_native_batches_cover_tail_documents_with_bounded_requests():
    from copy import deepcopy
    class BatchGateway(Gateway):
        async def generate_json(self, request):
            self.requests.append(request)
            payload = request.user_prompt.split("اسناد منبع:\n", 1)[1].split("\n\n", 1)[0]
            urls = [item["url"] for item in json.loads(payload)]
            content = deepcopy(source_content())
            content["findings"][0]["document_url"] = urls[-1]
            content["evidence_map"] = {u: "شاهد" for u in urls}
            return SimpleNamespace(content=content)
    gateway = BatchGateway({})
    docs = []
    for i in range(31):
        item = doc()
        item.url = item.canonical_url = f"https://example.invalid/news/{i}"
        docs.append(item)
    result = await SingleSourceAnalyzer(gateway).analyze_source(SourceAnalysisSpec(prompt_version="native-source-v2"), docs, "مذاکرات")
    assert len(gateway.requests) == 3
    assert result.raw_result["documents_analyzed"] == result.raw_result["documents_total"] == 31
    assert len(result.evidence_map) == 31
    assert "https://example.invalid/news/30" in result.evidence_map
    assert result.input_digest != (await SingleSourceAnalyzer(gateway).analyze_source(SourceAnalysisSpec(prompt_version="native-source-v2"), docs[:15], "مذاکرات")).input_digest


def test_source_count_survives_snapshot_and_applies_multi_source_budget():
    from app.modules.monitoring.domain.execution import SourceExecutionSpec
    from app.modules.monitoring.domain.crawl_policy import effective_crawl_policy
    spec = SourceExecutionSpec(uuid4(), "موضوع", date(2026,10,1), date(2026,10,3), {}, "https://example.invalid", "خبر", source_count=20)
    restored = SourceExecutionSpec.from_dict(spec.to_dict())
    assert restored.source_count == 20
    policy = effective_crawl_policy(restored.crawl_config, date_from=restored.date_from, date_to=restored.date_to, source_count=restored.source_count)
    assert policy.max_pages == 25 and policy.max_discovered_urls <= 1500


@pytest.mark.asyncio
async def test_native_synthesis_preserves_contract_and_provenance_without_requery():
    report = {"title":"گزارش", "executive_summary":{"text":"سنتز شناختی", "metrics":[]},
        "timeline":[], "sources_analysis":[], "cognitive_analysis":{"sub_sections":[{"title":"تحلیل روایت", "content":"مقایسه"}], "key_findings":["یافته"]},
        "future_analysis":{"scenarios":[], "key_variables":[]}, "conclusion":"جمع‌بندی", "sources":[]}
    gateway = Gateway(report)
    aggregate = await RunAggregatorAdapter(pinned_gateway_factory=lambda spec:gateway).aggregate(
        {"prompt_version":"native-source-v2", "model_provider":"gapgpt", "model_name":"pinned", "bot_identity":"ثابت"},
        [{"source_id":str(uuid4()), "source_analysis_result_id":str(uuid4()), "summary":"خلاصه منبع",
          "findings":[], "input_digest":"a"*64, "evidence_map":{"https://example.invalid/news/1":"شاهد"},
          "raw_result":{"source_title":"منبع", "document_headers":[]}}], {"expected_sources":2})
    assert aggregate["summary"] == "سنتز شناختی"
    assert aggregate["provenance_references"][0]["input_digest"] == "a"*64
    snapshot = aggregate["raw_result"]["report_snapshot"]
    assert snapshot["document"]["cognitive_analysis"]["key_findings"] == ["یافته"]
    assert snapshot["sources"][0]["url"] == "https://example.invalid/news/1"
    assert aggregate["coverage_metadata"]["expected_sources"] == 2
    assert len(gateway.requests) == 1

