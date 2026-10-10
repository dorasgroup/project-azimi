"""SQLAlchemy implementations of MonitoringRunRepository and SourceTaskRepository ports."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, case, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from app.shared.infrastructure.run_fence import lock_parent_for_child, lock_run
from app.shared.integration.outbox import enqueue

from app.shared.domain.errors import ConflictError, ValidationError
from app.models.monitoring_run import MonitoringRun as OrmMonitoringRun
from app.models.source_task import SourceTask as OrmSourceTask
from app.modules.monitoring.domain.execution import (
    SOURCE_TASK_LEASE_SECONDS,
    ClaimOutcome,
    ClaimStatus,
    MonitoringRun,
    MonitoringRunStatus,
    SourceExecutionSpec,
    SourceTask,
    SourceTaskStatus,
)


def _to_run_domain(orm: OrmMonitoringRun) -> MonitoringRun:
    return MonitoringRun(
        id=orm.id,
        monitoring_job_id=orm.monitoring_job_id,
        status=orm.status,
        expected_sources=orm.expected_sources,
        terminal_sources=orm.terminal_sources,
        successful_sources=orm.successful_sources,
        failed_sources=orm.failed_sources,
        cancelled_sources=orm.cancelled_sources,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        started_at=orm.started_at,
        finished_at=orm.finished_at,
        execution_mode=getattr(orm, "execution_mode", "legacy"),
    )


def _to_task_domain(orm: OrmSourceTask) -> SourceTask:
    spec = None
    if orm.execution_spec is not None and isinstance(orm.execution_spec, dict):
        spec = SourceExecutionSpec.from_dict(orm.execution_spec)
    return SourceTask(
        id=orm.id,
        run_id=orm.run_id,
        source_id=orm.source_id,
        ordinal=orm.ordinal,
        status=orm.status,
        documents_count=orm.documents_count,
        created_at=orm.created_at,
        updated_at=orm.updated_at,
        started_at=orm.started_at,
        finished_at=orm.finished_at,
        claimed_at=orm.claimed_at,
        lease_expires_at=orm.lease_expires_at,
        claim_token=orm.claim_token,
        attempt_count=orm.attempt_count,
        error_code=orm.error_code,
        error_message=orm.error_message,
        execution_spec=spec,
    )


class SqlAlchemyMonitoringRunRepository:
    """PostgreSQL adapter for MonitoringRun persistence."""

    def __init__(self, session: AsyncSession | async_sessionmaker[AsyncSession]) -> None:
        self._session = session

    async def lock_for_cancellation(self, run_id: UUID) -> MonitoringRun | None:
        async with self._get_session() as session:
            orm = await session.scalar(select(OrmMonitoringRun)
                .where(OrmMonitoringRun.id == run_id).with_for_update()
                .execution_options(populate_existing=True))
            return _to_run_domain(orm) if orm else None

    async def save_cancellation(self, run: MonitoringRun) -> None:
        async with self._get_session() as session:
            await session.execute(update(OrmMonitoringRun).where(OrmMonitoringRun.id == run.id)
                .values(status=run.status, finished_at=run.finished_at, updated_at=run.updated_at,
                        terminal_sources=run.terminal_sources, cancelled_sources=run.cancelled_sources))

    @asynccontextmanager
    async def _get_session(self) -> AsyncIterator[AsyncSession]:
        if isinstance(self._session, async_sessionmaker):
            async with self._session() as session:
                yield session
                await session.commit()
        else:
            yield self._session

    async def create(self, run: MonitoringRun) -> MonitoringRun:
        async with self._get_session() as session:
            orm = OrmMonitoringRun(
                id=run.id,
                monitoring_job_id=run.monitoring_job_id,
                status=run.status,
                expected_sources=run.expected_sources,
                terminal_sources=run.terminal_sources,
                successful_sources=run.successful_sources,
                failed_sources=run.failed_sources,
                cancelled_sources=run.cancelled_sources,
                created_at=run.created_at,
                updated_at=run.updated_at,
                started_at=run.started_at,
                finished_at=run.finished_at,
                execution_mode=run.execution_mode,
            )
            session.add(orm)
            await session.flush()
            return _to_run_domain(orm)

    async def get_by_id(self, run_id: UUID) -> MonitoringRun | None:
        async with self._get_session() as session:
            orm = await session.get(OrmMonitoringRun, run_id)
            return _to_run_domain(orm) if orm is not None else None

    async def get_active_for_job(self, job_id: UUID) -> MonitoringRun | None:
        stmt = (
            select(OrmMonitoringRun)
            .where(
                OrmMonitoringRun.monitoring_job_id == job_id,
                OrmMonitoringRun.status.in_([
                    MonitoringRunStatus.PENDING,
                    MonitoringRunStatus.RUNNING,
                ]),
            )
            .order_by(OrmMonitoringRun.created_at.desc())
        )
        async with self._get_session() as session:
            orm = await session.scalar(stmt)
            return _to_run_domain(orm) if orm is not None else None

    async def ensure_active_run(
        self,
        job_id: UUID,
        *,
        expected_sources: int,
        execution_mode: str = "legacy",
        now: datetime | None = None,
    ) -> MonitoringRun:
        async with self._get_session() as session:
            stmt = (
                select(OrmMonitoringRun)
                .where(
                    OrmMonitoringRun.monitoring_job_id == job_id,
                    OrmMonitoringRun.status.in_([
                        MonitoringRunStatus.PENDING,
                        MonitoringRunStatus.RUNNING,
                    ]),
                )
                .order_by(OrmMonitoringRun.created_at.desc())
            )
            active = await session.scalar(stmt)
            if active is not None:
                return _to_run_domain(active)

            current_time = now or datetime.now(UTC)
            insert_stmt = (
                insert(OrmMonitoringRun)
                .values(
                    id=uuid4(),
                    monitoring_job_id=job_id,
                    status=MonitoringRunStatus.PENDING,
                    execution_mode=execution_mode,
                    expected_sources=expected_sources,
                    terminal_sources=0,
                    successful_sources=0,
                    failed_sources=0,
                    cancelled_sources=0,
                    created_at=current_time,
                    updated_at=current_time,
                )
                .on_conflict_do_nothing(
                    index_elements=["monitoring_job_id"],
                    index_where=text("status IN ('pending', 'running')"),
                )
                .returning(OrmMonitoringRun.id)
            )
            res = await session.execute(insert_stmt)
            row = res.one_or_none()
            await session.flush()

            if row is not None:
                run_orm = await session.get(OrmMonitoringRun, row.id)
                if run_orm is not None:
                    return _to_run_domain(run_orm)

            active = await session.scalar(stmt)
            if active is not None:
                return _to_run_domain(active)

            raise RuntimeError(f"Failed to ensure active run for job {job_id}")

    async def finalize_run(
        self,
        run_id: UUID,
        target_status: MonitoringRunStatus = MonitoringRunStatus.COMPLETED,
        *,
        now: datetime | None = None,
    ) -> MonitoringRun | None:
        current_time = now or datetime.now(UTC)
        async with self._get_session() as session:
            orm = await session.get(OrmMonitoringRun, run_id, with_for_update=True, populate_existing=True)
            if orm is None:
                return None

            if orm.status in {
                MonitoringRunStatus.COMPLETED,
                MonitoringRunStatus.FAILED,
                MonitoringRunStatus.CANCELLED,
            }:
                return _to_run_domain(orm)

            if target_status == MonitoringRunStatus.COMPLETED:
                if orm.terminal_sources != orm.expected_sources:
                    raise ValidationError(
                        f"Cannot finalize run {run_id} as COMPLETED: "
                        f"{orm.terminal_sources} of {orm.expected_sources} terminal sources"
                    )

            orm.status = target_status
            orm.finished_at = current_time
            orm.updated_at = current_time
            await session.flush()
            return _to_run_domain(orm)

    async def transition_status(
        self,
        run_id: UUID,
        target_status: MonitoringRunStatus,
        now: datetime | None = None,
    ) -> MonitoringRun | None:
        async with self._get_session() as session:
            orm = await session.get(OrmMonitoringRun, run_id, with_for_update=True, populate_existing=True)
            if orm is None:
                return None

            domain_run = _to_run_domain(orm)
            updated_domain = domain_run.transition_to(target_status, now=now)

            orm.status = updated_domain.status
            orm.started_at = updated_domain.started_at
            orm.finished_at = updated_domain.finished_at
            orm.updated_at = updated_domain.updated_at

            await session.flush()
            return _to_run_domain(orm)

    async def update_counters(
        self,
        run_id: UUID,
        *,
        terminal_delta: int = 0,
        successful_delta: int = 0,
        success_delta: int | None = None,
        failed_delta: int = 0,
        cancelled_delta: int = 0,
        now: datetime | None = None,
    ) -> tuple[int, int, int]:
        actual_success_delta = successful_delta if success_delta is None else success_delta
        current_time = now or datetime.now(UTC)

        stmt = (
            update(OrmMonitoringRun)
            .where(OrmMonitoringRun.id == run_id)
            .values(
                terminal_sources=OrmMonitoringRun.terminal_sources + terminal_delta,
                successful_sources=OrmMonitoringRun.successful_sources + actual_success_delta,
                failed_sources=OrmMonitoringRun.failed_sources + failed_delta,
                cancelled_sources=OrmMonitoringRun.cancelled_sources + cancelled_delta,
                updated_at=current_time,
            )
            .returning(
                OrmMonitoringRun.terminal_sources,
                OrmMonitoringRun.successful_sources,
                OrmMonitoringRun.failed_sources,
            )
        )
        async with self._get_session() as session:
            res = await session.execute(stmt)
            row = res.one_or_none()
            await session.flush()
            if row is None:
                return 0, 0, 0
            return row.terminal_sources, row.successful_sources, row.failed_sources


class SqlAlchemySourceTaskRepository:
    """PostgreSQL adapter for SourceTask persistence."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _parent_status(self, task_id: UUID) -> str | None:
        return await lock_parent_for_child(
            self._session, OrmSourceTask.run_id, OrmSourceTask.id, task_id)

    async def _rejected_claim(self, outcome: ClaimOutcome) -> ClaimOutcome:
        # This immediate-commit claim API owns its transaction, including
        # read-only no-op outcomes. Release locks without expiring snapshots.
        await self._session.commit()
        return outcome

    async def cancel_for_run(self, run_id: UUID, now: datetime) -> int:
        rows = await self._session.scalars(update(OrmSourceTask)
            .where(OrmSourceTask.run_id == run_id,
                   OrmSourceTask.status.in_([SourceTaskStatus.PENDING, SourceTaskStatus.RUNNING]))
            .values(status=SourceTaskStatus.CANCELLED, finished_at=now, updated_at=now,
                    claim_token=None, lease_expires_at=None).returning(OrmSourceTask.id))
        return len(rows.all())

    async def ensure_tasks_for_sources(
        self,
        run_id: UUID,
        sources: Sequence[tuple[UUID, int]] | Sequence[dict[str, object]] | list,
    ) -> list[SourceTask]:
        if await lock_run(self._session, run_id) in (None, "cancelled"):
            return []
        current_time = datetime.now(UTC)
        tasks: list[SourceTask] = []

        for item in sources:
            spec_dict = None
            if isinstance(item, tuple):
                source_id, ordinal = item[0], item[1]
            elif isinstance(item, dict):
                source_id = item.get("id") or item.get("source_id")
                ordinal = item.get("ordinal", len(tasks) + 1)
                raw_spec = item.get("execution_spec")
                if isinstance(raw_spec, SourceExecutionSpec):
                    spec_dict = raw_spec.to_dict()
                elif isinstance(raw_spec, dict):
                    spec_dict = raw_spec
            else:
                source_id = getattr(item, "id", None) or getattr(item, "source_id", None)
                ordinal = getattr(item, "ordinal", len(tasks) + 1)
                raw_spec = getattr(item, "execution_spec", None)
                if isinstance(raw_spec, SourceExecutionSpec):
                    spec_dict = raw_spec.to_dict()
                elif isinstance(raw_spec, dict):
                    spec_dict = raw_spec

            if not source_id:
                continue

            insert_stmt = (
                insert(OrmSourceTask)
                .values(
                    id=uuid4(),
                    run_id=run_id,
                    source_id=source_id,
                    ordinal=ordinal,
                    status=SourceTaskStatus.PENDING,
                    execution_spec=spec_dict,
                    documents_count=0,
                    attempt_count=0,
                    created_at=current_time,
                    updated_at=current_time,
                )
                .on_conflict_do_nothing(
                    constraint="uq_source_tasks_run_id_source_id",
                )
                .returning(OrmSourceTask)
            )
            res = await self._session.execute(insert_stmt)
            orm = res.scalar_one_or_none()
            if orm is not None:
                tasks.append(_to_task_domain(orm))
            else:
                existing = await self.get_by_run_and_source(run_id, source_id)
                if existing is not None:
                    tasks.append(existing)

        await self._session.flush()
        return tasks

    async def get_by_id(self, task_id: UUID) -> SourceTask | None:
        orm = await self._session.get(OrmSourceTask, task_id, populate_existing=True)
        return _to_task_domain(orm) if orm is not None else None

    async def get_by_run_and_source(self, run_id: UUID, source_id: UUID) -> SourceTask | None:
        stmt = select(OrmSourceTask).where(
            OrmSourceTask.run_id == run_id,
            OrmSourceTask.source_id == source_id,
        )
        orm = await self._session.scalar(stmt)
        return _to_task_domain(orm) if orm is not None else None

    async def list_for_run(self, run_id: UUID) -> list[SourceTask]:
        stmt = (
            select(OrmSourceTask)
            .where(OrmSourceTask.run_id == run_id)
            .order_by(OrmSourceTask.ordinal.asc())
        )
        res = await self._session.scalars(stmt)
        return [_to_task_domain(orm) for orm in res.all()]

    async def claim_task_explicit(
        self,
        task_id: UUID,
        *,
        claim_token: UUID | None = None,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> ClaimOutcome:
        parent = await self._parent_status(task_id)
        if parent is None:
            return await self._rejected_claim(ClaimOutcome.not_found())
        if parent == "cancelled":
            return await self._rejected_claim(ClaimOutcome.run_cancelled())
        current_time = now or datetime.now(UTC)
        token = claim_token or uuid4()
        lease_expires = current_time + timedelta(seconds=lease_duration_seconds)

        subquery_cancelled = (
            select(OrmMonitoringRun.id)
            .where(OrmMonitoringRun.status == MonitoringRunStatus.CANCELLED)
        )

        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                or_(
                    OrmSourceTask.status == SourceTaskStatus.PENDING,
                    and_(
                        OrmSourceTask.status == SourceTaskStatus.RUNNING,
                        OrmSourceTask.lease_expires_at < current_time,
                    ),
                ),
                OrmSourceTask.run_id.notin_(subquery_cancelled),
            )
            .values(
                status=SourceTaskStatus.RUNNING,
                claimed_at=current_time,
                lease_expires_at=lease_expires,
                claim_token=token,
                attempt_count=OrmSourceTask.attempt_count + 1,
                started_at=case(
                    (OrmSourceTask.started_at.is_(None), current_time),
                    else_=OrmSourceTask.started_at,
                ),
                updated_at=current_time,
            )
            .returning(
                OrmSourceTask.id,
                OrmSourceTask.claim_token,
                OrmSourceTask.lease_expires_at,
                OrmSourceTask.attempt_count,
            )
        )
        res = await self._session.execute(stmt)
        row = res.one_or_none()
        await self._session.flush()
        if row is not None:
            await self._session.commit()
            return ClaimOutcome.claimed(
                claim_token=row.claim_token,
                lease_expires_at=row.lease_expires_at,
                attempt_count=row.attempt_count,
            )

        orm = await self._session.get(OrmSourceTask, task_id, populate_existing=True)
        if orm is None:
            return await self._rejected_claim(ClaimOutcome.not_found())

        run_orm = await self._session.get(OrmMonitoringRun, orm.run_id)
        if run_orm is not None and run_orm.status == MonitoringRunStatus.CANCELLED:
            return await self._rejected_claim(ClaimOutcome.run_cancelled())

        if orm.status in {
            SourceTaskStatus.COMPLETED,
            SourceTaskStatus.FAILED,
            SourceTaskStatus.CANCELLED,
        }:
            return await self._rejected_claim(ClaimOutcome.already_terminal(orm.status))

        if orm.status == SourceTaskStatus.RUNNING:
            if orm.lease_expires_at is not None and orm.lease_expires_at >= current_time:
                return await self._rejected_claim(ClaimOutcome.active_lease(orm.lease_expires_at))

        return await self._rejected_claim(ClaimOutcome.already_terminal(orm.status))

    async def claim_task(
        self,
        task_id: UUID,
        *,
        claim_token: UUID | None = None,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> tuple[bool, UUID | None]:
        outcome = await self.claim_task_explicit(
            task_id,
            claim_token=claim_token,
            lease_duration_seconds=lease_duration_seconds,
            now=now,
        )
        return outcome.is_claimed, outcome.claim_token

    async def renew_lease(
        self,
        task_id: UUID,
        *,
        claim_token: UUID,
        lease_duration_seconds: int = SOURCE_TASK_LEASE_SECONDS,
        now: datetime | None = None,
    ) -> bool:
        if not claim_token:
            return False
        if await self._parent_status(task_id) in (None, "cancelled"):
            await self._session.rollback()
            return False
        current_time = now or datetime.now(UTC)
        new_expires = current_time + timedelta(seconds=lease_duration_seconds)
        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status == SourceTaskStatus.RUNNING,
                OrmSourceTask.claim_token == claim_token,
            )
            .values(
                lease_expires_at=new_expires,
                updated_at=current_time,
            )
            .returning(OrmSourceTask.id)
        )
        res = await self._session.execute(stmt)
        row = res.one_or_none()
        await self._session.flush()
        if row is not None:
            await self._session.commit()
            return True
        await self._session.rollback()
        return False

    async def defer_task(self, task_id: UUID, *, claim_token: UUID, delay_seconds: int = 2) -> bool:
        if not 2 <= delay_seconds <= 86400:
            raise ValueError('Invalid crawl continuation delay')
        if await self._parent_status(task_id) not in ('pending', 'running'):
            await self._session.rollback()
            return False
        now = datetime.now(UTC)
        row = (await self._session.execute(update(OrmSourceTask).where(
            OrmSourceTask.id == task_id, OrmSourceTask.status == SourceTaskStatus.RUNNING,
            OrmSourceTask.claim_token == claim_token, OrmSourceTask.lease_expires_at > now,
        ).values(status=SourceTaskStatus.PENDING, claim_token=None, claimed_at=None,
                 lease_expires_at=None, updated_at=now).returning(OrmSourceTask.attempt_count))).one_or_none()
        if row is None:
            await self._session.rollback()
            return False
        await enqueue(self._session, message_key=f'source-continuation:{task_id}:{row.attempt_count}',
                      message_type='SOURCE_EXECUTION', payload={'schema_version': 1, 'source_task_id': str(task_id)},
                      available_at=now + timedelta(seconds=delay_seconds))
        await self._session.commit()
        return True

    async def complete_task_and_update_run(
        self,
        task_id: UUID,
        *,
        documents_count: int,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> bool:
        if not claim_token:
            return False
        if await self._parent_status(task_id) in (None, "cancelled"):
            return False
        current_time = now or datetime.now(UTC)

        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status == SourceTaskStatus.RUNNING,
                OrmSourceTask.claim_token == claim_token,
            )
            .values(
                status=SourceTaskStatus.COMPLETED,
                documents_count=documents_count,
                finished_at=current_time,
                lease_expires_at=None,
                claim_token=None,
                updated_at=current_time,
            )
            .returning(OrmSourceTask.id, OrmSourceTask.run_id)
        )
        res = await self._session.execute(stmt)
        row = res.one_or_none()
        if row is None:
            return False

        counter_stmt = (
            update(OrmMonitoringRun)
            .where(OrmMonitoringRun.id == row.run_id)
            .values(
                terminal_sources=OrmMonitoringRun.terminal_sources + 1,
                successful_sources=OrmMonitoringRun.successful_sources + 1,
                updated_at=current_time,
            )
        )
        await self._session.execute(counter_stmt)
        await self._session.flush()
        return True

    async def fail_task_and_update_run(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        claim_token: UUID,
        now: datetime | None = None,
    ) -> bool:
        if not claim_token:
            return False
        if await self._parent_status(task_id) in (None, "cancelled"):
            return False
        current_time = now or datetime.now(UTC)

        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status == SourceTaskStatus.RUNNING,
                OrmSourceTask.claim_token == claim_token,
            )
            .values(
                status=SourceTaskStatus.FAILED,
                finished_at=current_time,
                error_code=error_code,
                error_message=error_message,
                lease_expires_at=None,
                claim_token=None,
                updated_at=current_time,
            )
            .returning(OrmSourceTask.id, OrmSourceTask.run_id)
        )
        res = await self._session.execute(stmt)
        row = res.one_or_none()
        if row is None:
            return False

        counter_stmt = (
            update(OrmMonitoringRun)
            .where(OrmMonitoringRun.id == row.run_id)
            .values(
                terminal_sources=OrmMonitoringRun.terminal_sources + 1,
                failed_sources=OrmMonitoringRun.failed_sources + 1,
                updated_at=current_time,
            )
        )
        await self._session.execute(counter_stmt)
        await self._session.flush()
        return True

    async def complete_task(
        self,
        task_id: UUID,
        *,
        documents_count: int,
        now: datetime | None = None,
    ) -> bool:
        if await self._parent_status(task_id) in (None, "cancelled"):
            return False
        current_time = now or datetime.now(UTC)
        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status == SourceTaskStatus.RUNNING,
            )
            .values(
                status=SourceTaskStatus.COMPLETED,
                documents_count=documents_count,
                finished_at=current_time,
                lease_expires_at=None,
                claim_token=None,
                updated_at=current_time,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        return (res.rowcount or 0) > 0

    async def fail_task(
        self,
        task_id: UUID,
        *,
        error_code: str,
        error_message: str,
        now: datetime | None = None,
    ) -> bool:
        if await self._parent_status(task_id) in (None, "cancelled"):
            return False
        current_time = now or datetime.now(UTC)
        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status == SourceTaskStatus.RUNNING,
            )
            .values(
                status=SourceTaskStatus.FAILED,
                error_code=error_code,
                error_message=error_message,
                finished_at=current_time,
                lease_expires_at=None,
                claim_token=None,
                updated_at=current_time,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        return (res.rowcount or 0) > 0

    async def cancel_task(
        self,
        task_id: UUID,
        *,
        now: datetime | None = None,
    ) -> bool:
        await self._parent_status(task_id)
        current_time = now or datetime.now(UTC)
        stmt = (
            update(OrmSourceTask)
            .where(
                OrmSourceTask.id == task_id,
                OrmSourceTask.status.in_([SourceTaskStatus.PENDING, SourceTaskStatus.RUNNING]),
            )
            .values(
                status=SourceTaskStatus.CANCELLED,
                finished_at=current_time,
                lease_expires_at=None,
                claim_token=None,
                updated_at=current_time,
            )
        )
        res = await self._session.execute(stmt)
        await self._session.flush()
        return (res.rowcount or 0) > 0
