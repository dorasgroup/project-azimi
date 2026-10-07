"""Pure source-health classification shared by probes and monitoring crawls."""

from typing import Literal


ConnectionStatus = Literal["unknown", "success", "failed"]

TRANSIENT_FAILURE_MARKERS = (
    "timeout",
    "timed out",
    "time budget exhausted",
    "connect",
    "connection",
    "network",
    "temporary",
    "temporarily",
    "http 500",
    "http 502",
    "http 503",
    "http 504",
)


def connection_status(
    success: bool,
    http_status: int | None,
    reason: str | None,
) -> ConnectionStatus:
    """Classify reachability without considering topic/date crawl yield."""
    if success:
        return "success"
    reason_text = (reason or "").casefold()
    if http_status in {500, 502, 503, 504} or any(
        marker in reason_text for marker in TRANSIENT_FAILURE_MARKERS
    ):
        return "unknown"
    return "failed"
