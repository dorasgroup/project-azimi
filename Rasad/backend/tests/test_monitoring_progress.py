from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.api.operations.monitoring import _to_read
from app.models.enums import JobStatus
from app.modules.monitoring.domain.progress import (
    MonitoringProgressSnapshot,
    encode_progress,
)


def test_monitoring_read_exposes_structured_progress_without_internal_payload() -> None:
    source_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        topic="موضوع",
        description=None,
        status=JobStatus.CRAWLING,
        date_from_jalali="1405/06/01",
        date_to_jalali="1405/06/10",
        sources=[SimpleNamespace(id=source_id)],
        bot_identity_id=uuid4(),
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        finished_at=None,
        error_message=encode_progress(
            MonitoringProgressSnapshot(
                stage="crawling",
                percent=42,
                sources_completed=1,
                sources_total=3,
                pages_processed=17,
                documents_prepared=8,
                eta_seconds=90,
                message="در حال خزش",
            )
        ),
    )

    result = _to_read(job)

    assert result.error_message is None
    assert result.progress is not None
    assert result.progress.percent == 42
    assert result.progress.sources_completed == 1
    assert result.progress.pages_processed == 17
    assert result.progress.eta_seconds == 90
