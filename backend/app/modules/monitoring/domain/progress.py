"""Backward-compatible persistence for live monitoring progress."""

import json
from dataclasses import asdict, dataclass


PROGRESS_PREFIX = "__rasad_progress_v1__:"


@dataclass(frozen=True)
class MonitoringProgressSnapshot:
    stage: str
    percent: int
    sources_completed: int
    sources_total: int
    pages_processed: int
    documents_prepared: int
    eta_seconds: int | None
    message: str


def encode_progress(snapshot: MonitoringProgressSnapshot) -> str:
    return PROGRESS_PREFIX + json.dumps(
        asdict(snapshot), ensure_ascii=False, separators=(",", ":")
    )


def decode_progress(value: str | None) -> MonitoringProgressSnapshot | None:
    if not value or not value.startswith(PROGRESS_PREFIX):
        return None
    try:
        payload = json.loads(value.removeprefix(PROGRESS_PREFIX))
        return MonitoringProgressSnapshot(
            stage=str(payload["stage"]),
            percent=max(0, min(100, int(payload["percent"]))),
            sources_completed=max(0, int(payload["sources_completed"])),
            sources_total=max(0, int(payload["sources_total"])),
            pages_processed=max(0, int(payload["pages_processed"])),
            documents_prepared=max(0, int(payload["documents_prepared"])),
            eta_seconds=(
                max(0, int(payload["eta_seconds"]))
                if payload.get("eta_seconds") is not None
                else None
            ),
            message=str(payload["message"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
