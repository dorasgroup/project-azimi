from unittest import TestCase

from app.modules.reports.infrastructure.repository import SqlAlchemyReportRepository


class ReportRepositoryContractTests(TestCase):
    def test_repository_exposes_ownership_and_persistence_operations(self) -> None:
        for name in (
            "list_accessible",
            "get_accessible",
            "save",
            "delete",
            "delete_for_job",
            "flush",
        ):
            self.assertTrue(callable(getattr(SqlAlchemyReportRepository, name)))
