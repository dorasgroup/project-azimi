"""Focused FastAPI dependency providers for application operations."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.operations.bot_identities import BotIdentityOperations
from app.api.operations.health import HealthOperations
from app.api.operations.identity import AdminUserOperations, AuthOperations
from app.api.operations.monitoring import (
    MonitoringCommands,
    MonitoringOperations,
    MonitoringQueries,
)
from app.api.operations.observability import QueryOperations
from app.api.operations.reports import ReportOperations
from app.api.operations.sources import SourceOperations
from app.bootstrap.transaction import TransactionRunner
from app.bootstrap.run_cancellation import CancelJobRunsAdapter
from app.modules.monitoring.infrastructure.distributed_progress import DistributedProgressReader
from app.api.operations.system_connections import (
    ConnectionHealthOperations,
    ProviderSettingsOperations,
)
from app.db.session import SessionFactory
from app.core.config import settings
from app.modules.reports.infrastructure.operations import (
    ReportCommandAdapter,
    ReportQueryAdapter,
)
from app.modules.reports.infrastructure import LocalReportFileStorage, SqlAlchemyReportRepository
from app.modules.documents.infrastructure import SqlAlchemyDocumentRepository
from app.modules.crawling.infrastructure import SqlAlchemySourceRepository
from app.modules.crawling.infrastructure.source_operations import (
    SourceCommandAdapter,
    SourceQueryAdapter,
)
from app.modules.identity.infrastructure.operations import (
    AdminUserOperationsAdapter,
    IdentityOperationsAdapter,
)
from app.modules.identity.infrastructure import SqlAlchemyUserRepository
from app.modules.monitoring.infrastructure.bot_identity_operations import (
    BotIdentityCommandAdapter,
    BotIdentityQueryAdapter,
)
from app.modules.monitoring.infrastructure.bot_identity_repository import (
    SqlAlchemyBotIdentityRepository,
)
from app.modules.monitoring.infrastructure.dashboard_operations import DashboardQueryAdapter
from app.modules.monitoring.infrastructure.dashboard_read_model import DashboardReadModel
from app.modules.monitoring.infrastructure.log_operations import SystemLogOperationsAdapter
from app.modules.monitoring.infrastructure.system_log_read_model import SystemLogReadModel
from app.modules.monitoring.infrastructure.system_log_repository import (
    SqlAlchemySystemLogRepository,
)
from app.modules.platform_settings.infrastructure.connections import (
    DatabaseConnectionProbe,
    ModelProviderConnectionProbe,
    ProviderSettingsCommandAdapter,
    ProviderSettingsQueryAdapter,
    RedisConnectionProbe,
)
from app.modules.monitoring.api import (
    CancelMonitoringJob,
    CreateMonitoringJob,
    DeleteMonitoringJob,
    GetMonitoringJob,
    ListMonitoringJobs,
    RetryMonitoringJob,
    UpdateMonitoringJob,
)
from app.modules.monitoring.infrastructure import (
    CeleryMonitoringJobQueue,
    SqlAlchemyMonitoringJobRepository,
)
from app.modules.monitoring.infrastructure.output_cleaner import MonitoringOutputCleaner
from app.shared.domain.clock import SystemClock
from app.shared.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from app.shared.infrastructure.health import DatabaseReadinessProbe


def _transaction(session: AsyncSession) -> TransactionRunner:
    return TransactionRunner(SqlAlchemyUnitOfWork(session))


async def provide_auth_operations() -> AsyncIterator[AuthOperations]:
    async with SessionFactory() as session:
        yield AuthOperations(
            IdentityOperationsAdapter(SqlAlchemyUserRepository(session)),
            _transaction(session),
        )


async def provide_admin_user_operations() -> AsyncIterator[AdminUserOperations]:
    async with SessionFactory() as session:
        yield AdminUserOperations(
            AdminUserOperationsAdapter(SqlAlchemyUserRepository(session)),
            _transaction(session),
        )


async def provide_source_operations() -> AsyncIterator[SourceOperations]:
    async with SessionFactory() as session:
        repository = SqlAlchemySourceRepository(session)
        yield SourceOperations(
            SourceQueryAdapter(repository),
            SourceCommandAdapter(repository),
            _transaction(session),
        )


async def provide_bot_identity_operations() -> AsyncIterator[BotIdentityOperations]:
    async with SessionFactory() as session:
        repository = SqlAlchemyBotIdentityRepository(session)
        yield BotIdentityOperations(
            BotIdentityQueryAdapter(repository),
            BotIdentityCommandAdapter(repository),
            _transaction(session),
        )


async def provide_monitoring_operations() -> AsyncIterator[MonitoringOperations]:
    async with SessionFactory() as session:
        repository = SqlAlchemyMonitoringJobRepository(session)
        cleaner = MonitoringOutputCleaner(
            SqlAlchemyDocumentRepository(session),
            SqlAlchemyReportRepository(session),
            LocalReportFileStorage(),
        )
        yield MonitoringOperations(
            MonitoringQueries(
                list_jobs=ListMonitoringJobs(repository),
                get_job=GetMonitoringJob(repository),
            ),
            MonitoringCommands(
                create_job=CreateMonitoringJob(repository),
                update_job=UpdateMonitoringJob(repository, cleaner),
                retry_job=RetryMonitoringJob(repository, cleaner),
                cancel_job=CancelMonitoringJob(repository, SystemClock(), CancelJobRunsAdapter(session)),
                delete_job=DeleteMonitoringJob(repository, cleaner),
            ),
            CeleryMonitoringJobQueue(),
            _transaction(session),
            progress_reader=DistributedProgressReader(session),
        )


async def provide_report_operations() -> AsyncIterator[ReportOperations]:
    async with SessionFactory() as session:
        yield ReportOperations(
            ReportQueryAdapter(session),
            ReportCommandAdapter(session),
            _transaction(session),
        )


async def provide_query_operations() -> AsyncIterator[QueryOperations]:
    async with SessionFactory() as session:
        yield QueryOperations(
            DashboardQueryAdapter(DashboardReadModel(session)),
            SystemLogOperationsAdapter(
                SystemLogReadModel(session),
                SqlAlchemySystemLogRepository(session),
            ),
            _transaction(session),
        )


def provide_connection_health() -> ConnectionHealthOperations:
    provider_queries = ProviderSettingsQueryAdapter()
    return ConnectionHealthOperations(
        DatabaseConnectionProbe(SessionFactory),
        RedisConnectionProbe(settings.redis_url),
        ModelProviderConnectionProbe(),
        provider_queries,
    )


def provide_health_operations() -> HealthOperations:
    return HealthOperations(DatabaseReadinessProbe())


def provide_provider_settings() -> ProviderSettingsOperations:
    return ProviderSettingsOperations(ProviderSettingsCommandAdapter())


from app.api.operations.version import VersionOperations
from app.core.version import AppVersionMetadataProvider


def provide_version_operations() -> VersionOperations:
    return VersionOperations(AppVersionMetadataProvider(default_migration_revision="f15a2d74c901"))
