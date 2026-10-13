from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from jose import jwt

from app.api.deps import get_current_user
from app.core.config import Settings, parse_cors_allowed_origins, settings
from app.db.session import get_db_session
from app.main import app
from app.modules.documents.presentation.routers import router as documents_router
from app.modules.users.presentation.routers import router as users_router
from app.schemas.auth import CurrentUserResponse
from app.services.chat_service import ChatService
from app.services.upload_service import UploadService
from app.services.user_service import UserService


def _user(role: str = "user") -> CurrentUserResponse:
    return CurrentUserResponse(
        id=str(uuid4()),
        email=f"{role}@example.com",
        full_name=f"Test {role}",
        role=role,
        is_active=True,
    )


def _production_cors_app() -> FastAPI:
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_allowed_origins(
            "https://idea.haminyar.ir", app_env="production"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @cors_app.get("/probe")
    async def probe() -> dict:
        return {"status": "ok"}

    return cors_app


def test_prototype_routes_are_absent_by_default() -> None:
    with TestClient(app) as client:
        assert client.post("/api/v1/users", json={}).status_code == 404
        assert client.post("/api/v1/documents/upload-sessions").status_code == 404
        assert client.post("/api/v1/documents/finalize", json={}).status_code == 404


def test_prototype_routes_cannot_be_enabled_in_production() -> None:
    production = Settings(
        _env_file=None,
        APP_ENV="production",
        CORS_ALLOWED_ORIGINS="https://idea.haminyar.ir",
        ENABLE_PROTOTYPE_ROUTES=True,
    )
    development = Settings(
        _env_file=None,
        APP_ENV="development",
        CORS_ALLOWED_ORIGINS="http://localhost:3000",
        ENABLE_PROTOTYPE_ROUTES=True,
    )
    assert production.prototype_routes_enabled is False
    assert development.prototype_routes_enabled is True


def test_explicit_development_prototype_routes_require_authentication() -> None:
    development_app = FastAPI()
    development_app.include_router(users_router)
    development_app.include_router(documents_router)
    with TestClient(development_app) as client:
        assert client.post("/api/v1/users", json={}).status_code == 401
        assert client.post("/api/v1/documents/upload-sessions").status_code == 401
        assert client.post("/api/v1/documents/finalize", json={}).status_code == 401

    schema = development_app.openapi()
    for path in (
        "/api/v1/users",
        "/api/v1/documents/upload-sessions",
        "/api/v1/documents/finalize",
    ):
        assert schema["paths"][path]["post"]["security"] == [{"HTTPBearer": []}]


@pytest.mark.parametrize(
    "origin",
    [
        "http://idea.haminyar.ir",
        "https://www.idea.haminyar.ir",
        "http://localhost:3000",
        "https://idea.haminyar.ir.evil.example",
    ],
)
def test_production_cors_rejects_every_unapproved_origin(origin: str) -> None:
    with TestClient(_production_cors_app()) as client:
        response = client.options(
            "/probe",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_production_cors_accepts_only_exact_origin() -> None:
    with TestClient(_production_cors_app()) as client:
        allowed = client.options(
            "/probe",
            headers={
                "Origin": "https://idea.haminyar.ir",
                "Access-Control-Request-Method": "GET",
            },
        )
        trailing_slash = client.options(
            "/probe",
            headers={
                "Origin": "https://idea.haminyar.ir/",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://idea.haminyar.ir"
    assert trailing_slash.status_code == 400


def test_invalid_and_expired_tokens_are_rejected() -> None:
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "type": "access",
            "exp": datetime.now(UTC) - timedelta(minutes=1),
        },
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )
    with TestClient(app) as client:
        invalid = client.get(
            "/api/v1/uploads", headers={"Authorization": "Bearer invalid-token"}
        )
        expired_response = client.get(
            "/api/v1/uploads", headers={"Authorization": f"Bearer {expired}"}
        )
    assert invalid.status_code == 401
    assert expired_response.status_code == 401


def test_normal_user_cannot_administer_users(monkeypatch) -> None:
    async def current_user_override():
        return _user("user")

    app.dependency_overrides[get_current_user] = current_user_override
    monkeypatch.setattr(UserService, "list_users", AsyncMock(return_value=([], 0)))
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/users")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert response.status_code == 403


def test_super_admin_can_access_user_administration(monkeypatch) -> None:
    async def current_user_override():
        return _user("super_admin")

    async def session_override():
        yield SimpleNamespace()

    app.dependency_overrides[get_current_user] = current_user_override
    app.dependency_overrides[get_db_session] = session_override
    monkeypatch.setattr(UserService, "list_users", AsyncMock(return_value=([], 0)))
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/users")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db_session, None)
    assert response.status_code == 200
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_upload_mutation_is_scoped_to_actor() -> None:
    actor_id = uuid4()
    upload_id = uuid4()
    session = SimpleNamespace(commit=AsyncMock())
    service = UploadService(session)
    service.uploads.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(FileNotFoundError):
        await service.delete_upload(upload_id, actor_id)

    service.uploads.get_by_id.assert_awaited_once_with(upload_id, uploaded_by=actor_id)


@pytest.mark.asyncio
async def test_chat_document_query_is_scoped_to_actor() -> None:
    class EmptyResult:
        def all(self):
            return []

    actor_id = uuid4()
    session = SimpleNamespace(execute=AsyncMock(return_value=EmptyResult()), commit=AsyncMock())
    service = ChatService(session)
    service.logs.create = AsyncMock()

    await service.ask(
        user_id=actor_id,
        message="test",
        client_session_id="test-session",
        conversation_id=None,
        category_ids=[uuid4()],
    )

    statement = session.execute.await_args.args[0]
    assert "uploads.uploaded_by" in str(statement)
