import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import log_event, setup_logging
from app.middleware.request_logging import RequestObservabilityMiddleware
from app.modules.documents.presentation.routers import router as documents_router
from app.modules.users.presentation.routers import router as users_router
from app.observability.metrics import mark_current_process_dead, metrics_response
from app.shared.domain.exceptions import DomainException
from app.shared.presentation.exception_handler import (
    domain_exception_handler,
    global_exception_handler,
)

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    log_event(
        logger,
        logging.INFO,
        "application_started",
        outcome=(
            "async_ingestion_enabled"
            if settings.async_ingestion_enabled
            else "sync_ingestion_enabled"
        ),
    )
    yield
    mark_current_process_dead()


app = FastAPI(
    title="Ideation Core API",
    description="Core API based on Clean Architecture",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestObservabilityMiddleware)

# Exception Handlers
app.add_exception_handler(DomainException, domain_exception_handler)
app.add_exception_handler(Exception, global_exception_handler)

# Routers
app.include_router(api_router, prefix="/api/v1")
if settings.prototype_routes_enabled:
    app.include_router(users_router)
    app.include_router(documents_router)


@app.get("/health/live", tags=["Health"])
async def liveness_probe():
    return {"status": "UP"}


@app.get("/metrics", include_in_schema=False)
async def prometheus_metrics():
    return metrics_response()
