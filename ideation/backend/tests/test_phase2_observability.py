import asyncio
import json
import logging
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO, StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI, UploadFile
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

from app.core.config import settings
from app.core.logging import JsonFormatter
from app.middleware.request_logging import RequestObservabilityMiddleware
from app.observability.context import get_request_id
from app.observability.metrics import (
    CHAT_RESULTS,
    DEPENDENCY_REQUESTS,
    DEPENDENCY_TIMEOUTS,
    HTTP_IN_PROGRESS,
    HTTP_REQUESTS,
    HTTP_UNHANDLED_EXCEPTIONS,
    UPLOAD_INDEXING_RESULTS,
    UPLOAD_RESULTS,
    observe_chat,
    observe_dependency,
)
from app.services.file_validation_service import FileValidationError
from app.services.upload_service import UploadService


def _observed_app() -> FastAPI:
    observed = FastAPI()
    observed.add_middleware(RequestObservabilityMiddleware)

    @observed.get("/items/{item_id}")
    async def item(item_id: str) -> dict:
        return {"item": item_id, "request_id": get_request_id()}

    @observed.get("/explode")
    async def explode() -> None:
        raise RuntimeError("internal failure password=must-not-appear")

    @observed.exception_handler(Exception)
    async def exception_handler(_request, _exc):
        return JSONResponse(status_code=500, content={"error": "internal"})

    return observed


def test_request_id_generated_and_valid_value_preserved() -> None:
    with TestClient(_observed_app()) as client:
        generated = client.get("/items/one")
        preserved = client.get("/items/two", headers={"X-Request-ID": "safe-id_42:ok"})

    assert generated.headers["x-request-id"]
    assert generated.json()["request_id"] == generated.headers["x-request-id"]
    assert preserved.headers["x-request-id"] == "safe-id_42:ok"
    assert preserved.json()["request_id"] == "safe-id_42:ok"


@pytest.mark.parametrize("invalid", ["contains space", "x" * 129, "bad\x7fcontrol"])
def test_invalid_request_id_is_safely_replaced(invalid: str) -> None:
    with TestClient(_observed_app()) as client:
        response = client.get("/items/value", headers={"X-Request-ID": invalid})
    assert response.status_code == 200
    assert response.headers["x-request-id"] != invalid


def test_request_id_present_on_error_and_unhandled_metric_increments() -> None:
    before = HTTP_UNHANDLED_EXCEPTIONS.labels("GET", "/explode", "internal")._value.get()
    with TestClient(_observed_app(), raise_server_exceptions=False) as client:
        response = client.get("/explode")
    after = HTTP_UNHANDLED_EXCEPTIONS.labels("GET", "/explode", "internal")._value.get()
    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert after == before + 1


def test_request_context_does_not_leak_between_concurrent_requests() -> None:
    request_ids = [f"concurrent-{index}" for index in range(20)]
    with TestClient(_observed_app()) as client:
        with ThreadPoolExecutor(max_workers=8) as executor:
            responses = list(
                executor.map(
                    lambda value: client.get(f"/items/{value}", headers={"X-Request-ID": value}),
                    request_ids,
                )
            )
    observed = {response.json()["request_id"] for response in responses}
    assert observed == set(request_ids)
    assert get_request_id() is None


@pytest.mark.asyncio
async def test_in_progress_gauge_tracks_active_request() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def inner(_scope, _receive, send):
        entered.set()
        await release.wait()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RequestObservabilityMiddleware(inner)
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/active",
        "headers": [],
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    before = HTTP_IN_PROGRESS.labels("GET")._value.get()
    task = asyncio.create_task(middleware(scope, receive, send))
    await entered.wait()
    assert HTTP_IN_PROGRESS.labels("GET")._value.get() == before + 1
    release.set()
    await task
    assert HTTP_IN_PROGRESS.labels("GET")._value.get() == before


def test_structured_logging_uses_template_and_redacts_secrets() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    request_logger = logging.getLogger("app.requests")
    previous_level = request_logger.level
    request_logger.setLevel(logging.INFO)
    request_logger.addHandler(handler)
    try:
        with TestClient(_observed_app()) as client:
            response = client.get(
                f"/items/{uuid4()}",
                headers={
                    "Authorization": "Bearer secret-jwt",
                    "X-Request-ID": "logging-test",
                },
            )
        request_logger.warning(
            "password=hunter2 Authorization:Bearer secret-jwt access_token=secret"
        )
    finally:
        request_logger.removeHandler(handler)
        request_logger.setLevel(previous_level)

    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    request_record = next(item for item in records if item.get("event") == "http_request_completed")
    combined = stream.getvalue()
    assert request_record["route"] == "/items/{item_id}"
    assert request_record["request_id"] == "logging-test"
    assert response.json()["item"] not in combined
    assert "hunter2" not in combined
    assert "secret-jwt" not in combined
    assert "access_token=secret" not in combined


def test_http_metrics_increment_and_never_label_raw_ids() -> None:
    item_id = str(uuid4())
    before = HTTP_REQUESTS.labels("GET", "/items/{item_id}", "2xx")._value.get()
    with TestClient(_observed_app()) as client:
        response = client.get(f"/items/{item_id}")
    after = HTTP_REQUESTS.labels("GET", "/items/{item_id}", "2xx")._value.get()
    assert response.status_code == 200
    assert after == before + 1

    from app.observability.metrics import metrics_response

    text = metrics_response().body.decode()
    assert list(text_string_to_metric_families(text))
    assert item_id not in text
    assert "request_id=" not in text


def test_nested_fastapi_router_uses_complete_public_template() -> None:
    nested = FastAPI()
    router = APIRouter()

    @router.get("/items/{item_id}")
    async def nested_item(item_id: str) -> dict:
        return {"item": item_id}

    nested.include_router(router, prefix="/api/v1/nested")
    nested.add_middleware(RequestObservabilityMiddleware)
    labels = ("GET", "/api/v1/nested/items/{item_id}", "2xx")
    before = HTTP_REQUESTS.labels(*labels)._value.get()
    with TestClient(nested) as client:
        response = client.get(f"/api/v1/nested/items/{uuid4()}")
    assert response.status_code == 200
    assert HTTP_REQUESTS.labels(*labels)._value.get() == before + 1


@pytest.mark.asyncio
async def test_upload_success_duplicate_and_failure_metrics(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    user_id = uuid4()

    success_session = SimpleNamespace(commit=AsyncMock())
    success = UploadService(success_session)
    success.uploads.get_by_hash = AsyncMock(return_value=None)

    async def create(upload):
        upload.id = uuid4()
        return upload

    success.uploads.create = AsyncMock(side_effect=create)
    success.dify.create_document_by_file = AsyncMock(
        return_value={"document": {"id": str(uuid4())}}
    )
    success_before = UPLOAD_RESULTS.labels("success")._value.get()
    indexing_before = UPLOAD_INDEXING_RESULTS.labels("success")._value.get()
    response = await success.create_upload(
        user_id,
        UploadFile(filename="safe.txt", file=BytesIO(b"safe metric fixture")),
    )
    assert response.status == "indexed"
    assert UPLOAD_RESULTS.labels("success")._value.get() == success_before + 1
    assert UPLOAD_INDEXING_RESULTS.labels("success")._value.get() == indexing_before + 1

    existing = SimpleNamespace(
        id=uuid4(),
        original_filename="existing.txt",
        status="indexed",
        file_extension=".txt",
        dify_document_id=uuid4(),
        categories=[],
    )
    duplicate = UploadService(SimpleNamespace(commit=AsyncMock()))
    duplicate.uploads.get_by_hash = AsyncMock(return_value=existing)
    duplicate.dify.create_document_by_file = AsyncMock()
    duplicate_before = UPLOAD_RESULTS.labels("duplicate")._value.get()
    await duplicate.create_upload(
        user_id,
        UploadFile(filename="duplicate.txt", file=BytesIO(b"same")),
    )
    assert UPLOAD_RESULTS.labels("duplicate")._value.get() == duplicate_before + 1
    duplicate.dify.create_document_by_file.assert_not_awaited()

    failure_before = UPLOAD_RESULTS.labels("failure")._value.get()
    with pytest.raises(FileValidationError):
        await success.create_upload(
            user_id,
            UploadFile(filename="unsafe.exe", file=BytesIO(b"unsafe")),
        )
    assert UPLOAD_RESULTS.labels("failure")._value.get() == failure_before + 1


@pytest.mark.asyncio
async def test_chat_success_failure_and_dependency_timeout_metrics() -> None:
    @observe_chat
    async def succeeds():
        return SimpleNamespace(sources=[{"safe": True}])

    @observe_chat
    async def fails():
        raise RuntimeError("chat failed")

    success_before = CHAT_RESULTS.labels("success")._value.get()
    failure_before = CHAT_RESULTS.labels("failure")._value.get()
    await succeeds()
    with pytest.raises(RuntimeError):
        await fails()
    assert CHAT_RESULTS.labels("success")._value.get() == success_before + 1
    assert CHAT_RESULTS.labels("failure")._value.get() == failure_before + 1

    request_before = DEPENDENCY_REQUESTS.labels("dify", "chat", "timeout")._value.get()
    timeout_before = DEPENDENCY_TIMEOUTS.labels("dify", "chat")._value.get()
    with pytest.raises(httpx.ReadTimeout):
        async with observe_dependency("dify", "chat"):
            raise httpx.ReadTimeout("controlled timeout")
    assert DEPENDENCY_REQUESTS.labels("dify", "chat", "timeout")._value.get() == request_before + 1
    assert DEPENDENCY_TIMEOUTS.labels("dify", "chat")._value.get() == timeout_before + 1


@pytest.mark.asyncio
async def test_get_document_is_an_allowed_dependency_operation() -> None:
    request_before = DEPENDENCY_REQUESTS.labels("dify", "get_document", "success")._value.get()
    async with observe_dependency("dify", "get_document"):
        pass
    assert (
        DEPENDENCY_REQUESTS.labels("dify", "get_document", "success")._value.get()
        == request_before + 1
    )


def test_multiprocess_metrics_aggregate_two_workers(tmp_path) -> None:
    environment = os.environ.copy()
    environment["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    environment["PYTHONPATH"] = os.getcwd()
    increment = (
        "from app.observability.metrics import HTTP_REQUESTS; "
        "HTTP_REQUESTS.labels('GET','/two-worker-test','2xx').inc()"
    )
    for _ in range(2):
        subprocess.run([sys.executable, "-c", increment], check=True, env=environment)

    collect = (
        "from app.observability.metrics import metrics_response; "
        "print(metrics_response().body.decode(), end='')"
    )
    output = subprocess.run(
        [sys.executable, "-c", collect],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    ).stdout
    samples = [
        sample
        for family in text_string_to_metric_families(output)
        for sample in family.samples
        if sample.name == "ideation_http_requests_total"
        and sample.labels.get("route") == "/two-worker-test"
    ]
    assert len(samples) == 1
    assert samples[0].value == 2
