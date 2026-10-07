from uuid import UUID

from app.models import UserRole
from app.modules.monitoring.ports import DashboardReader
from app.schemas.dashboard import DashboardSummary


class DashboardQueryAdapter:
    def __init__(self, read_model: DashboardReader[DashboardSummary]) -> None:
        self._read_model = read_model

    async def summary(self, *, user_id: UUID, role: UserRole) -> DashboardSummary:
        return await self._read_model.summary(user_id=user_id, role=role)
