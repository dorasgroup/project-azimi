"""Cross-context handoff adapter owned by Analysis module implementing SourceAnalysisHandoffPort."""

from __future__ import annotations

import logging
import json
from typing import Any, Callable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.source_task import SourceTask as OrmSourceTask
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.monitoring_job import MonitoringJob
from app.models.bot_identity import BotIdentity
from app.modules.analysis.domain.run_analysis_spec import (
    RunAnalysisSpec,
    compute_run_spec_digest,
)
from app.modules.analysis.domain.source_analysis import (
    SourceAnalysisSpec,
    SourceAnalysisStatus,
)
from app.modules.analysis.infrastructure.run_analysis_spec_repository import (
    SqlAlchemyRunAnalysisSpecRepository,
)
from app.modules.analysis.infrastructure.source_analysis_repository import (
    SqlAlchemySourceAnalysisRepository,
)
from app.modules.analysis.infrastructure.spec_resolver import SourceAnalysisSpecResolver
from app.modules.analysis.ports.run_analysis_spec import (
    EnsureRunAnalysisSpecPort,
    RunAnalysisSpecRepositoryPort,
)
from app.modules.analysis.ports.source_analysis import (
    SourceAnalysisDispatcherPort,
    SourceAnalysisRepositoryPort,
)
from app.modules.monitoring.ports.analysis_handoff import SourceAnalysisHandoffPort
from app.shared.integration.outbox import enqueue
from app.shared.integration.consumer_ack import acknowledge_in_session
from app.shared.infrastructure.run_fence import lock_parent_for_child

logger = logging.getLogger("rasad.analysis.handoff_adapter")


class AnalysisHandoffAdapter(SourceAnalysisHandoffPort, EnsureRunAnalysisSpecPort):
    """Analysis-owned implementation of the monitoring -> analysis handoff port.

    Also implements EnsureRunAnalysisSpecPort to allow freezing run-level configuration
    at run inception or before source task dispatch.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | Callable[[], AsyncSession],
        analysis_dispatcher: SourceAnalysisDispatcherPort | None = None,
        spec_resolver: SourceAnalysisSpecResolver | None = None,
        analysis_repo_factory: Callable[[AsyncSession], SourceAnalysisRepositoryPort] | None = None,
        run_spec_repo_factory: Callable[[AsyncSession], RunAnalysisSpecRepositoryPort] | None = None,
        freeze_job_context: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._dispatcher = analysis_dispatcher
        self._spec_resolver = spec_resolver or SourceAnalysisSpecResolver()
        self._analysis_repo_factory = analysis_repo_factory or SqlAlchemySourceAnalysisRepository
        self._run_spec_repo_factory = run_spec_repo_factory or SqlAlchemyRunAnalysisSpecRepository
        self._freeze_job_context = freeze_job_context

    async def ensure_run_analysis_spec(
        self,
        run_id: UUID,
        custom_config: Mapping[str, Any] | None = None,
    ) -> RunAnalysisSpec:
        """Idempotently freeze and persist RunAnalysisSpec for a MonitoringRun."""
        async with self._session_factory() as session:
            run_spec_repo = self._run_spec_repo_factory(session)
            existing = await run_spec_repo.get_by_run_id(run_id)
            if existing is not None:
                return existing

            config = dict(custom_config or {})
            if self._freeze_job_context:
                run = await session.get(OrmMonitoringRun, run_id)
                job = await session.get(MonitoringJob, run.monitoring_job_id) if run else None
                bot = await session.get(BotIdentity, job.bot_identity_id) if job else None
                if job is None or bot is None:
                    raise ValueError("Analysis freeze requires a durable job and bot identity")
                config.update({
                    "topic": job.topic,
                    "date_range": f"{job.date_from_jalali} تا {job.date_to_jalali}",
                    "analysis_requirements": job.description or "",
                    "bot_identity": json.dumps({key: getattr(bot, key) for key in (
                        "title", "role_definition", "tone", "analysis_style",
                        "cognitive_framework", "output_structure", "source_policy",
                    )}, ensure_ascii=False),
                    "schema_version": "native-analysis-v2",
                })
            base_spec = self._spec_resolver.resolve_base_spec(custom_config=config)
            digest = compute_run_spec_digest(base_spec)
            run_spec = await run_spec_repo.ensure(
                run_id=run_id,
                spec=base_spec,
                spec_digest=digest,
            )
            await session.commit()
            return run_spec

    async def ensure_and_dispatch(
        self,
        source_task_id: UUID,
        run_id: UUID | None = None,
        source_id: UUID | None = None,
        topic: str = "",
        date_range: str = "",
        source_title: str = "",
        source_url: str = "",
        consumed_outbox_id: UUID | None = None,
    ) -> None:
        """Idempotently ensure SourceAnalysis entity exists and dispatch to analysis queue."""
        async with self._session_factory() as session:
            if await lock_parent_for_child(
                session, OrmSourceTask.run_id, OrmSourceTask.id, source_task_id
            ) in (None, "cancelled"):
                await acknowledge_in_session(session, consumed_outbox_id)
                await session.commit()
                return
            analysis_repo = self._analysis_repo_factory(session)
            run_spec_repo = self._run_spec_repo_factory(session)

            # 1. Check if already exists (idempotency)
            existing = await analysis_repo.get_by_source_task_id(source_task_id)
            if existing is not None:
                if existing.is_terminal():
                    logger.info(
                        "SourceAnalysis %s for task %s is already terminal (%s); skipping redispatch",
                        existing.id,
                        source_task_id,
                        existing.status,
                    )
                    await acknowledge_in_session(session, consumed_outbox_id)
                    await session.commit()
                    return
                analysis_id_to_dispatch = existing.id
            else:
                # 2. Resolve missing IDs if not passed
                if run_id is None or source_id is None:
                    task_row = (
                        await session.execute(
                            select(OrmSourceTask).where(OrmSourceTask.id == source_task_id)
                        )
                    ).scalar_one_or_none()
                    if task_row is not None:
                        run_id = run_id or task_row.run_id
                        source_id = source_id or task_row.source_id
                    else:
                        raise ValueError(f"Cannot resolve task metadata for source_task_id={source_task_id}")

                assert run_id is not None
                assert source_id is not None

                # 3. Ensure and load the frozen RunAnalysisSpec snapshot for this run
                run_spec = await run_spec_repo.get_by_run_id(run_id)
                if run_spec is None:
                    if self._spec_resolver.require_configured:
                        raise ValueError("Distributed analysis requires a pre-frozen run spec")
                    base_spec = self._spec_resolver.resolve_base_spec()
                    digest = compute_run_spec_digest(base_spec)
                    run_spec = await run_spec_repo.ensure(
                        run_id=run_id,
                        spec=base_spec,
                        spec_digest=digest,
                    )

                # Identity-only outbox deliveries get context from the immutable
                # task snapshot, never mutable source/job rows.
                task_row = await session.get(OrmSourceTask, source_task_id)
                task_spec = (task_row.execution_spec or {}) if task_row else {}
                frozen_topic = str(run_spec.spec.get("topic") or task_spec.get("topic") or topic)
                frozen_dates = str(run_spec.spec.get("date_range") or date_range)
                if not frozen_dates and task_spec.get("date_from") and task_spec.get("date_to"):
                    frozen_dates = f"{task_spec['date_from']} تا {task_spec['date_to']}"
                # Derive per-source SourceAnalysisSpec strictly from frozen inputs.
                spec = self._spec_resolver.derive_source_spec(
                    run_spec.spec,
                    topic=frozen_topic,
                    date_range=frozen_dates,
                    source_title=str(task_spec.get("title") or source_title),
                    source_url=str(task_spec.get("base_url") or source_url),
                )

                # 4. Idempotently insert row in DB
                analysis = await analysis_repo.ensure_for_source_task(
                    source_task_id=source_task_id,
                    run_id=run_id,
                    source_id=source_id,
                    spec=spec,
                )
                analysis_id_to_dispatch = analysis.id
            # The SourceAnalysis row and dispatch intent share one commit.
            await enqueue(
                session,
                message_key=f"source-analysis:{source_task_id}",
                message_type="SOURCE_ANALYSIS",
                payload={"schema_version": 1, "source_analysis_id": str(analysis_id_to_dispatch)},
            )
            await acknowledge_in_session(session, consumed_outbox_id)
            await session.commit()
