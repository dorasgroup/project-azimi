"""Dashboard and system-log transport operations."""

from uuid import UUID

from app.bootstrap.transaction import TransactionRunner
from app.modules.identity.domain.roles import UserRole
from app.modules.monitoring.ports.operations import DashboardQueries, SystemLogOperations
from app.shared.domain.logging import LogLevel


class QueryOperations:
    def __init__(
        self,
        dashboard: DashboardQueries,
        logs: SystemLogOperations,
        transaction: TransactionRunner,
    ) -> None:
        self._dashboard = dashboard
        self._logs = logs
        self._transaction = transaction

    async def dashboard(self, user_id: UUID, role: UserRole):
        return await self._dashboard.summary(user_id=user_id, role=role)

    async def logs(self, page: int, page_size: int, level: LogLevel | None, module: str | None):
        return await self._logs.list_logs(page, page_size, level=level, module=module)

    async def delete_resolved_logs(self) -> int:
        return await self._transaction.run(self._logs.delete_resolved_logs)
