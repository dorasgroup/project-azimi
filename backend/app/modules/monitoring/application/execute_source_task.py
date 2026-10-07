"""Pure Python use case for executing a single source task."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import time
import math
from typing import Callable
from uuid import UUID

from app.modules.monitoring.domain.execution import (
    SOURCE_TASK_HEARTBEAT_SECONDS,
    ClaimStatus,
    MonitoringRunStatus,
)
from app.modules.monitoring.ports.analysis_handoff import SourceAnalysisHandoffPort
from app.modules.monitoring.ports.completion import SourceTerminalPublisherPort
from app.modules.monitoring.ports.execution import (
    MonitoringRunRepositoryPort,
    SourceExecutionEnginePort,
    SourceResultPersisterPort,
    SourceTaskRepositoryPort,
)

logger = logging.getLogger("rasad.worker.source_task")


class ExecuteSourceTaskUseCase:
    """Orchestrates lease claim, heartbeat renewal, crawl execution, and fenced persistence."""

    def __init__(
        self,
        task_repository: SourceTaskRepositoryPort,
        run_repository: MonitoringRunRepositoryPort,
        execution_engine: SourceExecutionEnginePort,
        result_persister: SourceResultPersisterPort,
        heartbeat_interval_seconds: int = SOURCE_TASK_HEARTBEAT_SECONDS,
        analysis_handoff: SourceAnalysisHandoffPort | None = None,
        terminal_publisher: SourceTerminalPublisherPort | None = None,
        checkpoint_factory: Callable | None = None,
    ) -> None:
        self._task_repo = task_repository
        self._run_repo = run_repository
        self._engine = execution_engine
        self._persister = result_persister
        self._heartbeat_interval = heartbeat_interval_seconds
        self._analysis_handoff = analysis_handoff
        self._terminal_publisher = terminal_publisher
        self._checkpoint_factory = checkpoint_factory

    async def execute(self, task_id: UUID) -> dict[str, object]:
        outcome = await self._task_repo.claim_task_explicit(task_id)
        if not outcome.is_claimed:
            logger.info("Task %s could not be claimed: status=%s", task_id, outcome.status)
            if outcome.status == ClaimStatus.ALREADY_TERMINAL and outcome.current_status != "cancelled":
                # Crash recovery: ensure analysis handoff was triggered
                if self._analysis_handoff is not None:
                    try:
                        logger.info("Crash recovery: ensuring handoff for already-terminal task %s", task_id)
                        await self._analysis_handoff.ensure_and_dispatch(source_task_id=task_id)
                    except Exception as err:
                        logger.warning("Error during crash recovery handoff for task %s: %s", task_id, err)
                        raise

                # Crash recovery: ensure source terminal signal was published
                if self._terminal_publisher is not None:
                    try:
                        task = await self._task_repo.get_by_id(task_id)
                        if task:
                            self._terminal_publisher.publish_source_terminal(
                                run_id=task.run_id,
                                source_task_id=task.id,
                                outcome=task.status.value,
                                occurred_at=datetime.now(UTC),
                            )
                    except Exception as pub_err:
                        logger.warning("Failed to republish source terminal event: %s", pub_err)

            res: dict[str, object] = {"status": outcome.status.value, "task_id": str(task_id)}
            if outcome.lease_expires_at is not None:
                res["lease_expires_at"] = outcome.lease_expires_at.isoformat()
            return res

        claim_token = outcome.claim_token
        if claim_token is None:
            return {"status": "missing_token", "task_id": str(task_id)}

        task = await self._task_repo.get_by_id(task_id)
        if task is None:
            logger.error("Task %s or its execution_spec missing after claim", task_id)
            return {"status": "not_found", "task_id": str(task_id)}

        run = await self._run_repo.get_by_id(task.run_id)
        if run is None or run.status == MonitoringRunStatus.CANCELLED:
            logger.info("Run %s cancelled; aborting task %s", task.run_id, task_id)
            return {"status": "run_cancelled", "task_id": str(task_id)}

        job_id = run.monitoring_job_id
        source_id = task.source_id
        if task.execution_spec is None:
            failed = await self._persister.persist_failure(
                task_id=task_id, claim_token=claim_token, job_id=job_id,
                source_id=source_id, error=ValueError("Missing immutable source execution spec"),
            )
            return {"status": "failed" if failed else "fencing_rejected", "task_id": str(task_id)}

        # Heartbeat loop
        stop_heartbeat = asyncio.Event()
        heartbeat_failed = asyncio.Event()

        async def _heartbeat_loop():
            while not stop_heartbeat.is_set():
                try:
                    await asyncio.wait_for(stop_heartbeat.wait(), timeout=self._heartbeat_interval)
                    break
                except asyncio.TimeoutError:
                    renewed = await self._task_repo.renew_lease(task_id, claim_token=claim_token)
                    if not renewed:
                        logger.warning("Heartbeat renewal failed for task %s (token %s)", task_id, claim_token)
                        heartbeat_failed.set()
                        break

        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            async with asyncio.timeout(180):
                checkpoint = self._checkpoint_factory(task_id, claim_token, task.execution_spec) if self._checkpoint_factory else None
                continuation = {}
                if checkpoint is not None:
                    records = await checkpoint.load()
                    continuation = dict(records.get('continuation', {}))
                    continuation.setdefault('started_at', time.time())
                    await checkpoint.save('continuation', continuation)
                    cooldown_delay = math.ceil(float(records.get('host_cooldown', {}).get('until', 0)) - time.time())
                    if cooldown_delay > 0 and time.time() - continuation['started_at'] < 1800:
                        deferred = await self._task_repo.defer_task(task_id, claim_token=claim_token,
                                                                  delay_seconds=max(2, min(86400, cooldown_delay)))
                        return {'status': 'continuation_queued' if deferred else 'fence_rejected',
                                'task_id': str(task_id), 'waiting_for_source': True}
                if checkpoint is None:
                    crawl_result, prepared, crawl_policy = await self._engine.execute_crawl(task.execution_spec)
                else:
                    crawl_result, prepared, crawl_policy = await self._engine.execute_crawl(task.execution_spec, checkpoint=checkpoint)

            stop_heartbeat.set()
            await heartbeat_task

            if heartbeat_failed.is_set():
                logger.warning("Aborting persistence for task %s because heartbeat failed", task_id)
                return {"status": "heartbeat_lost", "task_id": str(task_id)}

            diagnostics = getattr(crawl_result, 'diagnostics', None)
            remaining = getattr(diagnostics, 'pending_pages', 0) + getattr(diagnostics, 'retryable_pages', 0)
            retry_probe = getattr(diagnostics, 'reason_code', '') == 'probe_failed' and bool(getattr(getattr(crawl_result, 'probe', None), 'retryable', False))
            continuation['probe_failures'] = int(continuation.get('probe_failures', 0)) + 1 if retry_probe else 0
            safety_expired = time.time() - continuation.get('started_at', time.time()) >= 1800
            continue_probe = retry_probe and continuation['probe_failures'] < 3
            if checkpoint is not None:
                await checkpoint.save('continuation', continuation)
            probe = getattr(crawl_result, 'probe', None)
            access_stopped = probe is not None and (not probe.success or probe.robots_allowed is False)
            should_continue = continue_probe if access_stopped else bool(remaining)
            if checkpoint is not None and should_continue and not safety_expired:
                records = await checkpoint.load()
                delay = max(2, min(86400, math.ceil(float(records.get('host_cooldown', {}).get('until', 0)) - time.time())))
                if continue_probe:
                    delay = max(delay, 10 * continuation['probe_failures'])
                kwargs = {'claim_token': claim_token}
                if delay > 2:
                    kwargs['delay_seconds'] = delay
                deferred = await self._task_repo.defer_task(task_id, **kwargs)
                return {'status': 'continuation_queued' if deferred else 'fence_rejected',
                        'task_id': str(task_id), 'remaining_pages': remaining}
            if checkpoint is not None and remaining:
                if safety_expired:
                    diagnostics.reason_code = 'crawl_safety_deadline'
                    suffix = ' سقف ایمنی سی‌دقیقه‌ای منبع تمام شد؛ صفحات باقی‌مانده حفظ شده‌اند و پوشش کامل اعلام نمی‌شود.'
                else:
                    suffix = ' ادامهٔ دریافت به دلیل دسترسی منبع متوقف شد؛ پیشرفت قبلی حفظ شده و پوشش کامل اعلام نمی‌شود.'
                diagnostics.summary = diagnostics.summary.replace('در صف:', 'پردازش‌نشده:') + suffix
                await checkpoint.save('final_diagnostics', diagnostics.model_dump(mode='json'))

            persisted = await self._persister.persist_success(
                task_id=task_id,
                claim_token=claim_token,
                job_id=job_id,
                source_id=source_id,
                crawl_result=crawl_result,
                prepared_batch=prepared,
                crawl_policy=crawl_policy,
            )
            if not persisted:
                return {"status": "fence_rejected", "task_id": str(task_id)}
            if checkpoint is not None and not remaining:
                try:
                    await checkpoint.discard()
                except Exception:
                    # Cleanup cannot undo committed snapshots or trigger false
                    # failure/duplicate analysis. Pending slices are never purged.
                    logger.warning('Temporary crawl checkpoint cleanup deferred for task %s', task_id)

        except Exception as exc:
            stop_heartbeat.set()
            await heartbeat_task
            logger.exception("Error executing source task %s", task_id)
            failed = await self._persister.persist_failure(
                task_id=task_id,
                claim_token=claim_token,
                job_id=job_id,
                source_id=source_id,
                error=exc,
            )

            if failed and self._terminal_publisher is not None:
                try:
                    self._terminal_publisher.publish_source_terminal(
                        run_id=run.id,
                        source_task_id=task_id,
                        outcome="failed",
                        occurred_at=datetime.now(UTC),
                    )
                except Exception as pub_err:
                    logger.warning("Failed to publish source terminal event on failure: %s", pub_err)

            return {"status": "failed", "task_id": str(task_id), "run_id": str(run.id), "error": str(exc)}

        finally:
            # External cancellation must not leave a detached lease heartbeat.
            stop_heartbeat.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

        # Trigger cross-context handoff to Analysis
        if self._analysis_handoff is not None:
            try:
                await self._analysis_handoff.ensure_and_dispatch(source_task_id=task_id)
            except Exception as handoff_err:
                logger.error("Handoff to analysis failed for task %s: %s", task_id, handoff_err)
                raise

        # Publish source terminal integration event
        if self._terminal_publisher is not None:
            try:
                self._terminal_publisher.publish_source_terminal(
                    run_id=run.id,
                    source_task_id=task_id,
                    outcome="completed",
                    occurred_at=datetime.now(UTC),
                )
            except Exception as pub_err:
                logger.warning("Failed to publish source terminal event: %s", pub_err)

        return {"status": "completed", "task_id": str(task_id), "run_id": str(run.id)}
