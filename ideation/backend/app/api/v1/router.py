from fastapi import APIRouter

from app.api.v1 import (
                          admin,
                          auth,
                          bale,
                          categories,
                          chat,
                          health,
                          integrations,
                          logs,
                          site_articles,
                          uploads,
)

api_router = APIRouter()
api_router.include_router(site_articles.router, prefix="/integrations/site-articles",
                          tags=["site-articles"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(
    integrations.router,
    prefix="/integrations/trends",
    tags=["trend-integrations"],
)
api_router.include_router(
    bale.router,
    prefix="/integrations/bale",
    tags=["bale-integration"],
)
api_router.include_router(categories.router, prefix="/categories", tags=["categories"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(logs.router, prefix="/logs", tags=["logs"])
