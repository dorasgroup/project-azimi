from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import settings
from app.modules.identity.domain.roles import UserRole

PASSWORD_ITERATIONS = 210_000


class TokenError(ValueError):
    pass


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${_b64url_encode(salt)}${_b64url_encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = _b64url_decode(salt_text)
        expected = _b64url_decode(digest_text)
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def refresh_token_expires_at() -> datetime:
    return datetime.now(UTC) + timedelta(days=settings.refresh_token_expire_days)


def create_access_token(subject: str, role: UserRole) -> tuple[str, int]:
    expires_in = settings.access_token_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role.value,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    header = {"alg": "HS256", "typ": "JWT"}
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}", expires_in


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        header_text, payload_text, signature_text = token.split(".", 2)
        signing_input = f"{header_text}.{payload_text}"
        expected_signature = hmac.new(
            settings.auth_secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected_signature, _b64url_decode(signature_text)):
            raise TokenError("Invalid token signature")
        payload = json.loads(_b64url_decode(payload_text))
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError("Invalid token") from exc

    if payload.get("typ") != "access":
        raise TokenError("Invalid token type")
    exp = payload.get("exp")
    if not isinstance(exp, int) or exp <= int(datetime.now(UTC).timestamp()):
        raise TokenError("Token expired")
    if not payload.get("sub") or payload.get("role") not in {role.value for role in UserRole}:
        raise TokenError("Invalid token claims")
    return payload
