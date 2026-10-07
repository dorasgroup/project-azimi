"""Validate broker-safe deadline capability before persisting a tracker."""

from datetime import UTC, datetime
from uuid import UUID

from app.modules.monitoring.ports.completion import (
    DeadlinePolicyPort,
    RunCompletionTrackerRepositoryPort,
)


class CreateRunCompletionTracker:
    def __init__(self, trackers: RunCompletionTrackerRepositoryPort, policy: DeadlinePolicyPort):
        self._trackers = trackers
        self._policy = policy

    async def execute(self, run_id: UUID, deadline_at: datetime | None):
        if deadline_at is not None:
            self._policy.validate(deadline_at, datetime.now(UTC))
        return await self._trackers.get_or_create(run_id, deadline_at=deadline_at)
