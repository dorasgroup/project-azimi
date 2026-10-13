from fastapi import APIRouter, Depends, Response, Request
from app.shared.presentation.dependencies import get_uow
from app.shared.application.unit_of_work import IUnitOfWork
from app.shared.application.dto import StandardResponse
from app.modules.auth.application.dto import LoginRequestDTO, TokenResponseDTO
from app.modules.auth.application.use_cases import LoginUseCase

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

@router.post("/login", response_model=StandardResponse[TokenResponseDTO])
async def login(
    req: LoginRequestDTO,
    response: Response,
    request: Request,
    uow: IUnitOfWork = Depends(get_uow)
):
    use_case = LoginUseCase(uow)
    token_dto, refresh_token = await use_case.execute(req)
    
    # Set HttpOnly cookie for Refresh Token
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=False, # Should be True in Production with HTTPS
        samesite="lax",
        max_age=7 * 24 * 60 * 60 # 7 days
    )

    return StandardResponse.ok(
        data=token_dto,
        message="Login successful.",
        trace_id=request.headers.get("X-Request-ID", "")
    )
