"""Celery implementation of DeadlineSchedulerPort."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from uuid import UUID

from celery import Celery

from app.core.config import settings
from app.modules.monitoring.ports.completion import (
    DeadlineSchedulerPort,
    TransientDeadlineSchedulingError,
    UnsupportedDeadlineHorizonError,
)

logger = logging.getLogger("rasad.monitoring.deadline_scheduler")

# Broker-safe horizon dynamically derived from runtime configuration (visibility timeout - safety margin)
DEFAULT_MAX_DEADLINE_HORIZON_SECONDS: int = settings.max_deadline_horizon_seconds


class CeleryDeadlineScheduler(DeadlineSchedulerPort):
    """Schedules a one-shot deadline control message to Celery queue with countdown/ETA.

    Deadline capability is supported only within configured broker-safe horizon.
    """

    def __init__(
        self,
        celery_app: Celery,
        max_horizon_seconds: int | None = None,
    ) -> None:
        self._celery_app = celery_app
        self._max_horizon_seconds = (
            max_horizon_seconds
            if max_horizon_seconds is not None
            else settings.max_deadline_horizon_seconds
        )

    def schedule_deadline_signal(
        self,
        run_id: UUID,
        tracker_id: UUID,
        deadline_at: datetime,
    ) -> None:
        """Schedule one-shot deadline_reached event to monitoring.control queue.

        Deadline capability is supported only within configured broker-safe horizon.
        """
        now = datetime.now(UTC)
        delay_seconds = (deadline_at - now).total_seconds()
        self.validate(deadline_at, now)

        delay_seconds = max(0.0, delay_seconds)

        payload = {
            "event": "deadline_reached",
            "run_id": str(run_id),
            "tracker_id": str(tracker_id),
            "deadline_at": deadline_at.isoformat(),
            "scheduled_at": now.isoformat(),
        }

        try:
            self._celery_app.send_task(
                "monitoring.process_control_event",
                args=[payload],
                countdown=int(delay_seconds),
                queue="monitoring.control",
            )
            logger.info(
                "Scheduled one-shot deadline signal for run %s at %s (countdown=%ss, max_horizon=%ss)",
                run_id,
                deadline_at.isoformat(),
                int(delay_seconds),
                self._max_horizon_seconds,
            )
        except UnsupportedDeadlineHorizonError:
            raise
        except Exception as exc:
            logger.warning(
                "Transient broker error scheduling deadline signal for run %s: %s",
                run_id,
                exc,
            )
            raise TransientDeadlineSchedulingError(
                f"Transient broker error scheduling deadline signal for run {run_id}: {exc}"
            ) from exc

    def validate(self, deadline_at: datetime, now: datetime) -> None:
        delay_seconds = (deadline_at - now).total_seconds()
        if delay_seconds > self._max_horizon_seconds:
            raise UnsupportedDeadlineHorizonError(
                f"Unsupported deadline horizon: {delay_seconds:.1f}s exceeds "
                f"broker-safe horizon of {self._max_horizon_seconds}s "
                "(visibility timeout minus safety margin). "
                "Deadline capability is supported only within configured broker-safe horizon."
            )
