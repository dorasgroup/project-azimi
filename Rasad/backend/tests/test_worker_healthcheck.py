"""Tests for Celery worker healthcheck probe."""

from unittest.mock import MagicMock, patch

from app.workers.healthcheck import check_worker_health


def test_worker_healthcheck_success() -> None:
    with patch("app.workers.healthcheck.Celery") as mock_celery_cls:
        mock_app = MagicMock()
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = {"celery@worker-1": {"ok": "pong"}}
        mock_app.control.inspect.return_value = mock_inspect
        mock_celery_cls.return_value = mock_app

        status = check_worker_health(timeout=1.0)
        assert status == 0
        mock_app.control.inspect.assert_called_once()


def test_worker_healthcheck_no_worker_response() -> None:
    with patch("app.workers.healthcheck.Celery") as mock_celery_cls:
        mock_app = MagicMock()
        mock_inspect = MagicMock()
        mock_inspect.ping.return_value = None
        mock_app.control.inspect.return_value = mock_inspect
        mock_celery_cls.return_value = mock_app

        status = check_worker_health(timeout=1.0)
        assert status == 1


def test_worker_healthcheck_exception_handling() -> None:
    with patch("app.workers.healthcheck.Celery") as mock_celery_cls:
        mock_celery_cls.side_effect = ConnectionError("Redis connection lost")

        status = check_worker_health(timeout=1.0)
        assert status == 1
