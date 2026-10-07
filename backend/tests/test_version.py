"""Tests for version and release metadata endpoint."""

import re
from starlette.testclient import TestClient

from app.main import app
from app.schemas.version import VersionResponse

HEX_DIGEST_REGEX = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
GIT_SHA_REGEX = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)


def test_version_endpoint_returns_expected_metadata() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "git_sha" in data
    assert "source_digest" in data
    assert "build_time" in data
    assert "migration_revision" in data

    validated = VersionResponse.model_validate(data)
    assert validated.version != ""

    # git_sha must be either a valid hex sha or 'unknown' - no human labels allowed!
    assert validated.git_sha == "unknown" or bool(GIT_SHA_REGEX.match(validated.git_sha))
    assert validated.git_sha not in {"manual-phase1", "latest", "phase1", "production"}

    # source_digest must be a 64-char hex sha256
    assert bool(HEX_DIGEST_REGEX.match(validated.source_digest))


def test_version_endpoint_does_not_leak_secrets() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    forbidden_keys = {"secret", "password", "token", "database_url", "auth_secret_key"}
    assert not any(key in data for key in forbidden_keys)
