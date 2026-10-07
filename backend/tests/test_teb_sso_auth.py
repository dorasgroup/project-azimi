import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import require_user
from app.models.enums import UserRole


def request_with_sso(value: str | None = None) -> Request:
    headers = [] if value is None else [(b"x-teb-sso", value.encode("utf-8"))]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.mark.asyncio
async def test_trusted_teb_sso_maps_to_super_admin(monkeypatch):
    monkeypatch.setenv("TEB_SSO_SECRET", "test-sso-secret")
    monkeypatch.setenv("TEB_SSO_USER_ID", "00000000-0000-0000-0000-000000000001")

    principal = await require_user(request_with_sso("test-sso-secret"), None)

    assert principal.subject == "00000000-0000-0000-0000-000000000001"
    assert principal.role == UserRole.SUPER_ADMIN


@pytest.mark.asyncio
@pytest.mark.parametrize("header", [None, "wrong-secret"])
async def test_missing_or_forged_teb_sso_is_rejected(monkeypatch, header):
    monkeypatch.setenv("TEB_SSO_SECRET", "test-sso-secret")
    monkeypatch.setenv("TEB_SSO_USER_ID", "00000000-0000-0000-0000-000000000001")

    with pytest.raises(HTTPException) as error:
        await require_user(request_with_sso(header), None)

    assert error.value.status_code == 401
