from fastapi import APIRouter, Depends, Request

from app.api.deps import require_super_admin
from app.modules.users.application.dto import CreateUserDTO, UserResponseDTO
from app.modules.users.application.use_cases import CreateUserUseCase
from app.shared.application.dto import StandardResponse
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.presentation.dependencies import get_uow

router = APIRouter(
    prefix="/api/v1/users",
    tags=["Users"],
    dependencies=[Depends(require_super_admin)],
)

@router.post("", response_model=StandardResponse[UserResponseDTO], status_code=201)
async def create_user(
    req: CreateUserDTO,
    request: Request,
    uow: IUnitOfWork = Depends(get_uow)
):
    use_case = CreateUserUseCase(uow)
    user_dto = await use_case.execute(req)
    
    return StandardResponse.ok(
        data=user_dto,
        message="User created successfully.",
        trace_id=request.headers.get("X-Request-ID", "")
    )
