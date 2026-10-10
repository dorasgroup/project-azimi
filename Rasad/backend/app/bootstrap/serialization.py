"""JSON-compatible serialization at worker transport boundaries."""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum


def json_ready(value: object) -> object:
    """Convert framework-independent records to Celery's JSON-safe primitives."""

    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: json_ready(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
