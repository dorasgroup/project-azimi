import asyncio

import httpx
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.services.dify_service import DifyService

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/health/live")
async def liveness() -> dict:
    """Process-only probe. External services must never affect liveness."""
    return {"status": "alive"}


@router.get("/health/ready", response_model=None)
async def readiness(session: AsyncSession = Depends(get_db_session)) -> JSONResponse:
    """Readiness requires only the metadata system of record (PostgreSQL)."""
    try:
        async with asyncio.timeout(2):
            await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "checks": {"postgres": "unavailable"}},
        )
    return JSONResponse(content={"status": "ready", "checks": {"postgres": "ok"}})


@router.get("/health/dify")
async def dify_health() -> dict:
    try:
        datasets = await DifyService().list_datasets()
        return {
            "status": "ok",
            "dify": {
                "datasets_total": datasets.get("total"),
                "message": "Dify API is reachable",
            },
        }
    except httpx.HTTPError:
        return {
            "status": "degraded",
            "dify": {
                "datasets_total": None,
                "message": "Dify API is unavailable",
            },
        }
