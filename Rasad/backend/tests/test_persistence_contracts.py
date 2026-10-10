from unittest import TestCase

from app.modules.analysis.infrastructure.job_reader import SqlAlchemyAnalysisJobReader
from app.modules.analysis.ports import AnalysisJobReader
from app.modules.crawling.infrastructure.source_repository import SqlAlchemySourceRepository
from app.modules.crawling.ports import SourceRepository
from app.modules.documents.infrastructure.repository import SqlAlchemyDocumentRepository
from app.modules.documents.ports import DocumentRepository
from app.modules.identity.infrastructure.repository import SqlAlchemyUserRepository
from app.modules.identity.ports import UserRepository
from app.modules.monitoring.infrastructure.bot_identity_repository import (
    SqlAlchemyBotIdentityRepository,
)
from app.modules.monitoring.infrastructure.dashboard_read_model import DashboardReadModel
from app.modules.monitoring.infrastructure.repository import SqlAlchemyMonitoringJobRepository
from app.modules.monitoring.infrastructure.system_log_read_model import SystemLogReadModel
from app.modules.monitoring.infrastructure.system_log_repository import (
    SqlAlchemySystemLogRepository,
)
from app.modules.monitoring.ports import (
    BotIdentityRepository,
    DashboardReader,
    MonitoringJobRepository,
    SystemLogReader,
    SystemLogRepository,
)
from app.modules.reports.infrastructure.file_storage import LocalReportFileStorage
from app.modules.reports.infrastructure.repository import SqlAlchemyReportRepository
from app.modules.reports.ports import ReportFileStorage, ReportRepository


class _SessionStub:
    """Construction-only session; contract checks must not perform I/O."""


class PersistenceContractTests(TestCase):
    def test_sqlalchemy_repositories_implement_owned_ports(self) -> None:
        session = _SessionStub()
        adapters = (
            (SqlAlchemyUserRepository(session), UserRepository),
            (SqlAlchemySourceRepository(session), SourceRepository),
            (SqlAlchemyMonitoringJobRepository(session), MonitoringJobRepository),
            (SqlAlchemyDocumentRepository(session), DocumentRepository),
            (SqlAlchemyReportRepository(session), ReportRepository),
            (SqlAlchemyBotIdentityRepository(session), BotIdentityRepository),
            (SqlAlchemySystemLogRepository(session), SystemLogRepository),
        )
        for adapter, contract in adapters:
            with self.subTest(adapter=type(adapter).__name__):
                self.assertIsInstance(adapter, contract)

    def test_read_models_implement_query_ports(self) -> None:
        session = _SessionStub()
        self.assertIsInstance(SqlAlchemyAnalysisJobReader(session), AnalysisJobReader)
        self.assertIsInstance(DashboardReadModel(session), DashboardReader)
        self.assertIsInstance(SystemLogReadModel(session), SystemLogReader)

    def test_report_storage_implements_storage_port(self) -> None:
        self.assertIsInstance(LocalReportFileStorage(), ReportFileStorage)
