from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_log import SystemLog
from app.repositories.system_log_repository import SystemLogRepository
from app.schemas.system_log import FrontendLogRequest, SystemLogResponse


class SystemLogService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = SystemLogRepository(session)
        self.session = session

    async def create_frontend_log(
        self,
        payload: FrontendLogRequest,
        *,
        actor_user_id: UUID | None,
        request_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> SystemLogResponse:
        log = SystemLog(
            level=payload.level,
            event_type=f"frontend.{payload.event_type}",
            actor_user_id=actor_user_id,
            request_id=request_id,
            ip_address=ip_address,
            user_agent=user_agent,
            resource_type="frontend",
            message=payload.message,
            metadata_json=payload.metadata,
        )
        created = await self.repository.create(log)
        await self.session.commit()
        return self._to_response(created)

    async def list_logs(
        self,
        *,
        page: int,
        limit: int,
        level: str | None,
        event_type: str | None,
    ) -> tuple[list[SystemLogResponse], int]:
        items, total = await self.repository.list_logs(
            page=page,
            limit=limit,
            level=level,
            event_type=event_type,
        )
        return [self._to_response(item) for item in items], total

    async def clear_logs(self) -> int:
        deleted_count = await self.repository.delete_all()
        await self.session.commit()
        return deleted_count

    def _to_response(self, log: SystemLog) -> SystemLogResponse:
        return SystemLogResponse(
            id=log.id,
            level=log.level,
            event_type=log.event_type,
            actor_user_id=log.actor_user_id,
            request_id=log.request_id,
            ip_address=str(log.ip_address) if log.ip_address else None,
            user_agent=log.user_agent,
            resource_type=log.resource_type,
            resource_id=log.resource_id,
            message=log.message,
            metadata=log.metadata_json,
            created_at=log.created_at,
        )
