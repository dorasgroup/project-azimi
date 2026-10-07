"""Use case for dispatching distributed source tasks in a fire-and-forget controller."""

from __future__ import annotations

import logging
import inspect

from app.modules.monitoring.application.types import MonitoringExecutionContext
from app.modules.monitoring.domain.execution import SourceExecutionSpec
from app.modules.monitoring.ports.execution import (
    MonitoringRunRepositoryPort,
    SourceTaskDispatcherPort,
    SourceTaskRepositoryPort,
)

logger = logging.getLogger("rasad.monitoring.fanout")


class FanOutSourceTasksUseCase:
    """Idempotently creates SourceTasks with immutable execution specs and dispatches them."""

    def __init__(
        self,
        run_repository: MonitoringRunRepositoryPort,
        task_repository: SourceTaskRepositoryPort,
        dispatcher: SourceTaskDispatcherPort,
        ensure_run_spec_port: object | None = None,
    ) -> None:
        self._run_repo = run_repository
        self._task_repo = task_repository
        self._dispatcher = dispatcher
        self._ensure_run_spec_port = ensure_run_spec_port

    async def fan_out(
        self,
        context: MonitoringExecutionContext,
    ) -> dict[str, object]:
        run = await self._run_repo.get_active_for_job(context.job_id)
        if run is None and context.run_id is not None:
            run = await self._run_repo.get_by_id(context.run_id)
        if run is None:
            run = await self._run_repo.ensure_active_run(
                context.job_id,
                expected_sources=len(context.sources),
                execution_mode="distributed",
            )

        if run.execution_mode == "legacy":
            from app.shared.domain.errors import ConflictError
            raise ConflictError(
                f"Cannot execute distributed fan-out on a run with execution_mode '{run.execution_mode}'"
            )

        if run.status.value == "cancelled":
            return {"status": "run_cancelled", "run_id": str(run.id), "dispatched_tasks": 0}

        # Freeze run-level analysis configuration before dispatching source tasks
        if self._ensure_run_spec_port is not None:
            await self._ensure_run_spec_port.ensure_run_analysis_spec(
                run.id,
                custom_config=getattr(context, "analysis_config", None),
            )

        search_topic = (
            f"{context.topic} {context.description}" if context.description else context.topic
        )

        source_specs: list[dict[str, object]] = []
        for idx, src in enumerate(context.sources, start=1):
            spec = SourceExecutionSpec(
                source_id=src["id"],
                topic=search_topic,
                date_from=context.date_from,
                date_to=context.date_to,
                crawl_config=src.get("crawl_config") or {},
                base_url=src.get("base_url") or "",
                title=src.get("title") or "",
                spec_version=2,
                source_count=len(context.sources),
            )
            source_specs.append({
                "id": src["id"],
                "ordinal": idx,
                "execution_spec": spec,
            })

        tasks = await self._task_repo.ensure_tasks_for_sources(run.id, source_specs)

        logger.info(
            "Fan-out dispatching %s source tasks for job %s (run %s)",
            len(tasks),
            context.job_id,
            run.id,
        )

        for task in tasks:
            pending = self._dispatcher.dispatch(task.id)
            if inspect.isawaitable(pending):
                await pending

        return {
            "status": "dispatched",
            "run_id": str(run.id),
            "job_id": str(context.job_id),
            "dispatched_tasks": len(tasks),
        }
