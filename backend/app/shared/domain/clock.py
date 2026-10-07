"""Time abstraction for deterministic application logic."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Supplies the current UTC time to application and domain code."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock implementation."""

    def now(self) -> datetime:
        return datetime.now(UTC)
