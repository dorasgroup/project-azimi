"""Business deadline validation, independent of Redis visibility timeout."""

from datetime import datetime

from app.modules.monitoring.ports.completion import DeadlinePolicyPort


class PostgresDeadlinePolicy(DeadlinePolicyPort):
    def validate(self, deadline_at: datetime, now: datetime) -> None:
        if deadline_at.tzinfo is None or now.tzinfo is None:
            raise ValueError("Deadline must be timezone-aware")
