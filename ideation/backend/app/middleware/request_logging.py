import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import log_event
from app.observability.context import reset_request_id, set_request_id
from app.observability.metrics import (
    EXCLUDED_HTTP_PATHS,
    HTTP_DURATION,
    HTTP_IN_PROGRESS,
    HTTP_REQUESTS,
    HTTP_UNHANDLED_EXCEPTIONS,
    bounded_exception_type,
)

logger = logging.getLogger("app.requests")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def valid_request_id(value: str | None) -> bool:
    return bool(value and _REQUEST_ID.fullmatch(value))


def route_template(scope: Scope) -> str:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    included_router = scope.get("fastapi", {}).get("included_router")
    include_context = getattr(included_router, "include_context", None)
    prefix = getattr(include_context, "prefix", None)
    if isinstance(prefix, str) and isinstance(route_path, str):
        return f"{prefix}{route_path}" or "/"

    endpoint = scope.get("endpoint")
    application = scope.get("app")
    for candidate in getattr(application, "routes", ()):
        if endpoint is not None and getattr(candidate, "endpoint", None) is endpoint:
            path = getattr(candidate, "path", None)
            if isinstance(path, str) and path.startswith("/"):
                return path
    if isinstance(route_path, str) and route_path.startswith("/"):
        return route_path
    return "unmatched"


class RequestObservabilityMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = supplied if valid_request_id(supplied) else str(uuid4())
        scope.setdefault("state", {})["request_id"] = request_id
        context_token = set_request_id(request_id)
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        started = perf_counter()
        status_code = 500
        response_started = False
        excluded = path in EXCLUDED_HTTP_PATHS
        if not excluded:
            HTTP_IN_PROGRESS.labels(method).inc()

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers = [
                    item for item in response_headers if item[0].lower() != b"x-request-id"
                ]
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        escaped_exception: Exception | None = None
        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            escaped_exception = exc
            if response_started:
                raise
            from app.shared.presentation.exception_handler import global_exception_handler

            response = await global_exception_handler(Request(scope, receive=receive), exc)
            await response(scope, receive, send_with_request_id)
        finally:
            duration_seconds = perf_counter() - started
            route = route_template(scope)
            if not excluded:
                HTTP_IN_PROGRESS.labels(method).dec()
                HTTP_REQUESTS.labels(method, route, f"{status_code // 100}xx").inc()
                HTTP_DURATION.labels(method, route).observe(duration_seconds)
                if escaped_exception is not None:
                    HTTP_UNHANDLED_EXCEPTIONS.labels(
                        method,
                        route,
                        bounded_exception_type(escaped_exception),
                    ).inc()
                level = logging.ERROR if status_code >= 500 else logging.INFO
                log_event(
                    logger,
                    level,
                    "http_request_completed",
                    request_id=request_id,
                    method=method,
                    route=route,
                    status_code=status_code,
                    duration_ms=round(duration_seconds * 1000, 3),
                    outcome=(
                        "server_error"
                        if status_code >= 500
                        else "client_error"
                        if status_code >= 400
                        else "success"
                    ),
                )
            reset_request_id(context_token)
