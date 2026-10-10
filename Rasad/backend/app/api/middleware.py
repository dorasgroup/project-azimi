from __future__ import annotations

import logging
import time
from uuid import UUID, uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.logging import reset_request_id, set_request_id
from app.db.session import SessionFactory
from app.shared.domain.logging import LogLevel
from app.modules.monitoring.infrastructure.log_operations import record_system_log

logger = logging.getLogger("rasad.api")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id_text = request.headers.get("x-request-id") or str(uuid4())
        request_id = self._parse_request_id(request_id_text)
        token = set_request_id(str(request_id))
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = str(request_id)
            return response
        except Exception as exc:
            await self._record_error(request, request_id, exc)
            logger.exception(
                "Unhandled request error",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            level = logging.WARNING if status_code >= 400 else logging.INFO
            logger.log(
                level,
                "HTTP request completed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
            )
            reset_request_id(token)

    @staticmethod
    def _parse_request_id(value: str) -> UUID:
        try:
            return UUID(value)
        except ValueError:
            return uuid4()

    @staticmethod
    async def _record_error(request: Request, request_id: UUID, exc: Exception) -> None:
        try:
            async with SessionFactory() as session:
                await record_system_log(
                    session,
                    level=LogLevel.ERROR,
                    module="api",
                    message="Unhandled request error",
                    request_id=request_id,
                    context={
                        "method": request.method,
                        "path": request.url.path,
                        "error_type": exc.__class__.__name__,
                    },
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to persist system log")
