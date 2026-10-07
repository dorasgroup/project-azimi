"""Late ACK closes only durable terminal report obligations."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from app.workers import report_tasks


@pytest.mark.parametrize("status", ["completed", "failed", "run_cancelled", "already_terminal"])
def test_terminal_transport_acks_only_after_application_returns(status):
    with patch.object(report_tasks, "run_report_generation", return_value={"status": status}) as run, patch.object(report_tasks, "acknowledge_outbox") as ack:
        assert report_tasks.generate_report.run(str(uuid4()))["status"] == status
        run.assert_called_once()
        ack.assert_called_once()


@pytest.mark.parametrize("status", ["active_lease", "claim_lost", "not_found"])
def test_nonterminal_transport_retries_without_logical_ack(status):
    expiry = (datetime.now(UTC) + timedelta(seconds=100)).isoformat()
    with patch.object(report_tasks, "run_report_generation", return_value={"status": status, "lease_expires_at": expiry}), patch.object(report_tasks, "acknowledge_outbox") as ack, patch.object(report_tasks.generate_report, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            report_tasks.generate_report.run(str(uuid4()))
        ack.assert_not_called()
        assert retry.call_args.kwargs["max_retries"] is None
        if status == "active_lease":
            assert retry.call_args.kwargs["countdown"] >= 100


def test_unknown_failure_is_not_success_or_content_leak():
    with patch.object(report_tasks, "run_report_generation", side_effect=OSError("private-path-secret")), patch.object(report_tasks, "acknowledge_outbox") as ack, patch.object(report_tasks.generate_report, "retry", side_effect=Retry()) as retry:
        with pytest.raises(Retry):
            report_tasks.generate_report.run(str(uuid4()))
        ack.assert_not_called()
        assert "secret" not in str(retry.call_args.kwargs["exc"])


def test_delivery_settings_and_queues():
    for task in (report_tasks.generate_report, report_tasks.ensure_generation, report_tasks.finalize_report):
        assert task.acks_late and task.reject_on_worker_lost
        assert task.acks_on_failure_or_timeout is False
    assert report_tasks.generate_report.soft_time_limit == 240
    assert report_tasks.generate_report.time_limit == 300
    assert report_tasks.celery_app.conf.worker_prefetch_multiplier == 1
    assert report_tasks.celery_app.conf.task_routes["report.generate_report"]["queue"] == "report.generate"
