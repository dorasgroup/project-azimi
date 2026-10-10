"""Transactional outbox dispatcher for source tasks."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.monitoring.ports.execution import SourceTaskDispatcherPort
from app.shared.integration.outbox import enqueue
from app.models.source_task import SourceTask
from app.shared.infrastructure.run_fence import lock_parent_for_child


class OutboxSourceTaskDispatcher(SourceTaskDispatcherPort):
    """Records source task intent in the fan-out transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def dispatch(self, source_task_id: UUID) -> None:
        if await lock_parent_for_child(
            self._session, SourceTask.run_id, SourceTask.id, source_task_id
        ) in (None, "cancelled"):
            return
        await enqueue(
            self._session,
            message_key=f"source-execute:{source_task_id}",
            message_type="SOURCE_EXECUTION",
            payload={"schema_version": 1, "source_task_id": str(source_task_id)},
        )
