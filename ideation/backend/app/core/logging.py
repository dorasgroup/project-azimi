import json
import logging
import re
import tempfile
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import settings
from app.observability.context import get_request_id

_STRUCTURED_FIELDS = (
    "event",
    "request_id",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "dependency",
    "operation",
    "outcome",
    "error_type",
    "stack_trace",
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|password|api[_-]?key|access_token|refresh_token)"
    r"\s*[:=]\s*((?:bearer\s+)?[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")


def redact_log_text(value: str) -> str:
    redacted = _BEARER_TOKEN.sub("Bearer [REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", redacted)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": settings.service_name,
            "environment": settings.app_env,
            "message": redact_log_text(record.getMessage()),
            "request_id": getattr(record, "request_id", None) or get_request_id(),
        }
        for field in _STRUCTURED_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class DevelopmentFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or get_request_id() or "-"
        return (
            f"{datetime.now(UTC).isoformat(timespec='milliseconds')} "
            f"{record.levelname} [{record.name}] request_id={request_id} "
            f"{redact_log_text(record.getMessage())}"
        )


def log_event(logger: logging.Logger, level: int, event: str, **fields) -> None:
    safe_fields = {key: value for key, value in fields.items() if key in _STRUCTURED_FIELDS}
    safe_fields["event"] = event
    logger.log(level, event, extra=safe_fields)


def _log_directory() -> Path:
    try:
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        return settings.log_dir
    except PermissionError:
        fallback = Path(tempfile.gettempdir()) / "ideation-rag-logs"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def setup_logging() -> None:
    formatter: logging.Formatter
    if settings.app_env.strip().lower() in {"production", "prod"}:
        formatter = JsonFormatter()
    else:
        formatter = DevelopmentFormatter()

    log_dir = _log_directory()
    app_handler = RotatingFileHandler(
        log_dir / "backend.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(formatter)

    error_handler = RotatingFileHandler(
        log_dir / "backend-errors.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(app_handler)
    root.addHandler(error_handler)
    root.addHandler(console_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").disabled = True
