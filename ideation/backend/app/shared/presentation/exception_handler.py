import logging
import traceback
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse

from app.core.logging import log_event
from app.observability.context import get_request_id
from app.shared.application.dto import ErrorDetail, StandardResponse
from app.shared.domain.exceptions import DomainException

logger = logging.getLogger(__name__)


def get_trace_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or get_request_id() or "unavailable"


def _safe_stack_locations(exc: Exception) -> list[str]:
    return [
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        for frame in traceback.extract_tb(exc.__traceback__)[-12:]
    ]


async def domain_exception_handler(request: Request, exc: DomainException):
    trace_id = get_trace_id(request)
    level = logging.ERROR if exc.status_code >= 500 else logging.INFO
    log_event(
        logger,
        level,
        "domain_exception",
        request_id=trace_id,
        method=request.method,
        status_code=exc.status_code,
        outcome="expected_error",
        error_type="domain",
    )

    errors = [ErrorDetail(code=exc.code, detail=exc.message)]
    if exc.details:
        for field, detail in exc.details.items():
            errors.append(ErrorDetail(code=exc.code, field=field, detail=str(detail)))

    response = StandardResponse.error(
        message=exc.message,
        errors=errors,
        trace_id=trace_id,
    )
    return JSONResponse(status_code=exc.status_code, content=response.model_dump())


async def global_exception_handler(request: Request, exc: Exception):
    trace_id = get_trace_id(request)
    log_event(
        logger,
        logging.ERROR,
        "unhandled_exception",
        request_id=trace_id,
        method=request.method,
        status_code=500,
        outcome="server_error",
        error_type=type(exc).__name__,
        stack_trace=_safe_stack_locations(exc),
    )

    response = StandardResponse.error(
        message="An unexpected internal server error occurred.",
        errors=[ErrorDetail(code="INTERNAL_SERVER_ERROR", detail="Please contact support.")],
        trace_id=trace_id,
    )
    return JSONResponse(status_code=500, content=response.model_dump())
