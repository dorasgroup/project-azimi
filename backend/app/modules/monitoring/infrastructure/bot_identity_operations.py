from math import ceil
from uuid import UUID

from app.models import BotIdentity
from app.modules.monitoring.ports import BotIdentityRepository
from app.schemas.bot_identity import BotIdentityCreate, BotIdentityRead, BotIdentityUpdate
from app.schemas.common import PageMeta, PaginatedResponse


def identity_to_read(identity: BotIdentity) -> BotIdentityRead:
    return BotIdentityRead(
        id=identity.id,
        title=identity.title,
        description=identity.description,
        role_definition=identity.role_definition,
        tone=identity.tone,
        analysis_style=identity.analysis_style,
        cognitive_framework=identity.cognitive_framework,
        output_structure=identity.output_structure,
        source_policy=identity.source_policy,
        is_active=identity.is_active,
        is_default=identity.is_default,
    )


class BotIdentityInUseError(RuntimeError):
    pass


class BotIdentityQueryAdapter:
    def __init__(self, repository: BotIdentityRepository) -> None:
        self._repository = repository

    async def list_identities(
        self, page: int, page_size: int
    ) -> PaginatedResponse[BotIdentityRead]:
        identities, total = await self._repository.list(page, page_size)
        return PaginatedResponse(
            items=[identity_to_read(identity) for identity in identities],
            meta=PageMeta(
                page=page,
                page_size=page_size,
                total=total,
                pages=ceil(total / page_size) if total else 0,
            ),
        )

    async def active_identity(self) -> BotIdentityRead | None:
        identity = await self._repository.active_default()
        return identity_to_read(identity) if identity else None


class BotIdentityCommandAdapter:
    def __init__(self, repository: BotIdentityRepository) -> None:
        self._repository = repository

    async def create_identity(
        self, payload: BotIdentityCreate, creator_id: UUID
    ) -> BotIdentityRead:
        has_default = await self._repository.has_default()
        identity = BotIdentity(
            title=payload.title,
            description=payload.description,
            role_definition=payload.role_definition,
            tone=payload.tone,
            analysis_style=payload.analysis_style,
            cognitive_framework=payload.cognitive_framework,
            output_structure=payload.output_structure,
            source_policy=payload.source_policy,
            is_active=payload.is_active,
            is_default=not has_default and payload.is_active,
            created_by=creator_id,
        )
        await self._repository.add(identity)
        return identity_to_read(identity)

    async def set_active_identity(self, identity_id: UUID) -> BotIdentityRead | None:
        identity = await self._repository.get(identity_id)
        if identity is None or not identity.is_active:
            return None
        await self._repository.set_default(identity)
        return identity_to_read(identity)

    async def update_identity(
        self, identity_id: UUID, payload: BotIdentityUpdate
    ) -> BotIdentityRead | None:
        identity = await self._repository.get(identity_id)
        if identity is None:
            return None
        identity.title = payload.title
        identity.description = payload.description
        identity.role_definition = payload.role_definition
        identity.tone = payload.tone
        identity.analysis_style = payload.analysis_style
        identity.cognitive_framework = payload.cognitive_framework
        identity.output_structure = payload.output_structure
        identity.source_policy = payload.source_policy
        identity.is_active = payload.is_active
        if identity.is_default and not payload.is_active:
            identity.is_default = False
        await self._repository.flush()
        return identity_to_read(identity)

    async def delete_identity(self, identity_id: UUID) -> bool:
        identity = await self._repository.get(identity_id)
        if identity is None:
            return False
        if identity.is_default:
            raise BotIdentityInUseError("هویت فعال اصلی سیستم قابل حذف نیست.")

        await self._repository.delete_with_jobs(identity)
        return True
