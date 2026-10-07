from unittest.mock import AsyncMock, patch
import json
import httpx
import pytest
from app.crawler.security import UnsafeUrlError
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy, _worker_payload
from app.modules.crawling.infrastructure.http_transport import SafeHttpTransport
from app.modules.crawling.infrastructure.source_probe import SourceProbeService

URL = "https://www.theguardian.com/world"

def response(status=200):
    return httpx.Response(status, headers={"Content-Type":"text/html"},request=httpx.Request("GET",URL))

def transport(policy):
    return SafeHttpTransport(user_agent="RasadCrawler",timeout_ms=8000,egress=policy)

def test_head_metadata_is_explicit_but_get_compatibility_is_preserved():
    assert "method" not in json.loads(_worker_payload(URL,{}))
    assert json.loads(_worker_payload(URL,{},method="HEAD"))["method"] == "HEAD"

@pytest.mark.asyncio
async def test_worker_probe_uses_head_not_get():
    policy=CrawlEgressPolicy(mode="smart",worker_url="https://worker.invalid",worker_secret="test")
    head=AsyncMock(return_value=response())
    with patch.object(CrawlEgressPolicy,"validate",new=AsyncMock(return_value=URL)),patch.object(CrawlEgressPolicy,"fetch_through_worker",new=head):
        result=await transport(policy).probe(URL)
    assert result.status_code == 200
    assert head.await_count == 1 and head.call_args.kwargs["method"] == "HEAD"

@pytest.mark.asyncio
@pytest.mark.parametrize("status",[405,501])
async def test_unsupported_head_falls_back_exactly_once(status):
    policy=CrawlEgressPolicy(mode="smart",worker_url="https://worker.invalid",worker_secret="test")
    head=AsyncMock(return_value=response(status))
    client=transport(policy)
    client.get=AsyncMock(return_value=response())
    with patch.object(CrawlEgressPolicy,"validate",new=AsyncMock(return_value=URL)),patch.object(CrawlEgressPolicy,"fetch_through_worker",new=head):
        assert (await client.probe(URL)).status_code == 200
    client.get.assert_awaited_once_with(URL)

@pytest.mark.asyncio
async def test_direct_sources_keep_existing_get_behavior():
    client=transport(CrawlEgressPolicy())
    client.get=AsyncMock(return_value=response())
    with patch.object(CrawlEgressPolicy,"validate",new=AsyncMock(return_value=URL)):
        await client.probe(URL)
    client.get.assert_awaited_once_with(URL)

@pytest.mark.asyncio
async def test_robots_denial_survives_head_success():
    robots=AsyncMock(return_value=False)
    result=await SourceProbeService().probe(URL,AsyncMock(return_value=response()),robots)
    assert result.success and result.robots_allowed is False
    robots.assert_awaited_once_with(URL)

@pytest.mark.asyncio
async def test_worker_never_permits_arbitrary_origin_methods():
    with pytest.raises(UnsafeUrlError,match="Unsupported"):
        await CrawlEgressPolicy(mode="smart").fetch_through_worker(URL,headers={},timeout_seconds=1,method="POST")

@pytest.mark.asyncio
async def test_worker_gateway_has_separate_non_reusing_pool_and_both_pools_close():
    policy = CrawlEgressPolicy(mode="smart", worker_url="https://worker.invalid", worker_secret="test")
    client = transport(policy)
    factory = httpx.AsyncClient
    fetch = AsyncMock(return_value=response())
    with patch("app.modules.crawling.infrastructure.http_transport.httpx.AsyncClient", wraps=factory) as construct:
        direct = client._shared_client()
        with patch.object(CrawlEgressPolicy, "validate", new=AsyncMock(return_value=URL)), patch.object(CrawlEgressPolicy, "fetch_through_worker", new=fetch):
            await client.probe(URL)
            await client.get(URL)
        worker = fetch.call_args_list[0].kwargs["client"]
        assert worker is fetch.call_args_list[1].kwargs["client"] and worker is not direct
        worker_options = construct.call_args_list[1].kwargs
        assert worker_options["limits"].max_keepalive_connections == 0
        assert "headers" not in worker_options and "proxy" not in worker_options
        assert construct.call_args_list[0].kwargs["limits"].max_keepalive_connections == 12
        await client._close()
        assert direct.is_closed and worker.is_closed
        assert client._client is client._worker_client is None
