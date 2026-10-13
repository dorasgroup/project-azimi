from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.api.v1.chat import send_message
from app.core.config import parse_cors_allowed_origins, settings
from app.db.session import get_db_session
from app.main import app
from app.observability.metrics import AI_FALLBACKS
from app.schemas.auth import CurrentUserResponse
from app.schemas.chat import ChatMessageRequest
from app.services.dify_service import DifyService
from app.services.file_validation_service import FileValidationError, FileValidationService
from app.services.upload_service import UploadService


def _cors_app(raw_origins: str) -> FastAPI:
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_allowed_origins(raw_origins, app_env="development"),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @cors_app.get("/probe")
    async def probe() -> dict:
        return {"status": "ok"}

    return cors_app


def _preflight(client: TestClient, origin: str):
    return client.options(
        "/probe",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )


def test_cors_allows_configured_origin() -> None:
    with TestClient(_cors_app("https://app.example.com")) as client:
        response = _preflight(client, "https://app.example.com")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"


def test_cors_rejects_unconfigured_origin() -> None:
    with TestClient(_cors_app("https://app.example.com")) as client:
        response = _preflight(client, "https://other.example.com")
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_cors_parses_multiple_trimmed_origins() -> None:
    assert parse_cors_allowed_origins(
        "  https://one.example.com , https://two.example.com  ",
        app_env="production",
    ) == ["https://one.example.com", "https://two.example.com"]


def test_cors_empty_production_configuration_fails() -> None:
    with pytest.raises(ValueError, match="must contain at least one"):
        parse_cors_allowed_origins("  , ", app_env="production")


def test_cors_wildcard_with_credentials_fails() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        parse_cors_allowed_origins("*", app_env="development", allow_credentials=True)


def test_cors_development_origin_is_rejected_in_production() -> None:
    with pytest.raises(ValueError, match="Development CORS origins"):
        parse_cors_allowed_origins("http://localhost:3000", app_env="production")


def test_application_starts_and_liveness_is_process_only() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.parametrize(
    ("db_error", "expected_status", "expected_body"),
    [
        (None, 200, {"status": "ready", "checks": {"postgres": "ok"}}),
        (
            RuntimeError("database unavailable"),
            503,
            {"status": "not_ready", "checks": {"postgres": "unavailable"}},
        ),
    ],
)
def test_readiness_reflects_postgres_without_exposing_details(
    db_error, expected_status, expected_body
) -> None:
    session = SimpleNamespace(execute=AsyncMock(side_effect=db_error))

    async def override_session():
        yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/health/ready")
    finally:
        app.dependency_overrides.pop(get_db_session, None)

    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert "database unavailable" not in response.text


def test_authentication_and_admin_routes_require_credentials() -> None:
    with TestClient(app) as client:
        me = client.get("/api/v1/auth/me")
        admin = client.get("/api/v1/admin/users")
    assert me.status_code == 401
    assert admin.status_code == 401


def test_upload_validation_rejects_unsupported_type_and_oversize(monkeypatch) -> None:
    validator = FileValidationService()
    with pytest.raises(FileValidationError, match="not supported"):
        validator.validate_filename("payload.exe")
    monkeypatch.setattr(settings, "upload_max_size_mb", 1)
    with pytest.raises(FileValidationError, match="larger"):
        validator.validate_size(1 * 1024 * 1024 + 1)


@pytest.mark.asyncio
async def test_upload_duplicate_detection_uses_sha256(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    session = SimpleNamespace(commit=AsyncMock())
    service = UploadService(session)
    existing_id = uuid4()
    existing = SimpleNamespace(
        id=existing_id,
        original_filename="existing.txt",
        status="indexed",
        file_extension=".txt",
        dify_document_id=uuid4(),
        categories=[],
    )
    service.uploads.get_by_hash = AsyncMock(return_value=existing)

    user_id = uuid4()
    response = await service.create_upload(
        user_id,
        UploadFile(filename="duplicate.txt", file=BytesIO(b"same-content")),
    )

    expected_hash = "cae1b3faaa5e4ac7c3306bd164b36dcfdff98294b8024c9c949639b4c480bf6b"
    service.uploads.get_by_hash.assert_awaited_once_with(expected_hash, user_id)
    assert response.id == str(existing_id)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_records_degraded_state_when_dify_is_unavailable(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "upload_storage_path", tmp_path)
    session = SimpleNamespace(commit=AsyncMock())
    service = UploadService(session)
    service.uploads.get_by_hash = AsyncMock(return_value=None)

    async def create(upload):
        upload.id = uuid4()
        return upload

    service.uploads.create = AsyncMock(side_effect=create)
    service.dify.create_document_by_file = AsyncMock(
        side_effect=httpx.ConnectError("dependency unavailable")
    )

    response = await service.create_upload(
        uuid4(),
        UploadFile(filename="small.txt", file=BytesIO(b"small safe fixture")),
    )

    assert response.status == "indexing_failed"
    assert response.message == "File uploaded locally, but Dify indexing failed"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_chat_api_maps_dify_connection_failure_to_502(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.chat.ChatService.ask",
        AsyncMock(side_effect=httpx.ConnectError("dependency unavailable")),
    )
    user = CurrentUserResponse(
        id=str(uuid4()),
        email="user@example.com",
        full_name="Test User",
        role="user",
        is_active=True,
    )
    with pytest.raises(HTTPException) as caught:
        await send_message(ChatMessageRequest(message="test"), user, AsyncMock())
    assert getattr(caught.value, "status_code", None) == 502


@pytest.mark.asyncio
async def test_weaviate_failure_uses_dify_retrieval_fallback(monkeypatch) -> None:
    class FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("dependency unavailable")

    service = DifyService()
    service._retrieve_via_dify_api = AsyncMock(return_value=[{"text": "fallback"}])
    monkeypatch.setattr(httpx, "AsyncClient", lambda **_kwargs: FailingClient())

    before = AI_FALLBACKS.labels("weaviate", "dify", "transport_error")._value.get()
    result = await service.retrieve_knowledge_chunks("query", limit=3)

    assert result == [{"text": "fallback"}]
    service._retrieve_via_dify_api.assert_awaited_once_with("query", 3)
    assert AI_FALLBACKS.labels("weaviate", "dify", "transport_error")._value.get() == before + 1
