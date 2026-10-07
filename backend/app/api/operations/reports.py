"""Report transport operations."""

from pathlib import Path
from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.identity.domain.roles import UserRole
from app.modules.reports.ports.operations import ReportCommands, ReportQueries
from app.shared.domain.errors import NotFoundError


class ReportOperations:
    def __init__(
        self,
        queries: ReportQueries,
        commands: ReportCommands,
        transaction: TransactionRunner,
    ) -> None:
        self._queries = queries
        self._commands = commands
        self._transaction = transaction

    async def list(self, page: int, page_size: int, user_id: UUID, role: UserRole):
        return await self._queries.list_reports(page, page_size, user_id=user_id, role=role)

    async def get(self, report_id: UUID, user_id: UUID, role: UserRole):
        result = await self._queries.get_report(report_id, user_id=user_id, role=role)
        if result is None:
            raise NotFoundError("Report not found")
        return result

    async def delete(self, report_id: UUID, user_id: UUID, role: UserRole) -> None:
        deleted = await self._transaction.run(
            lambda: self._commands.delete_report(report_id, user_id=user_id, role=role)
        )
        if not deleted:
            raise NotFoundError("Report not found")

    async def export(
        self, report_id: UUID, export_format: str, user_id: UUID, role: UserRole
    ) -> Path:
        path = await self._transaction.run(
            lambda: self._commands.export_path(report_id, export_format, user_id=user_id, role=role)
        )
        if path is None:
            raise NotFoundError("Report not found")
        return path
