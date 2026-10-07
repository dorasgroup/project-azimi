"""Emit the phase-two source audit and a bounded live capability smoke test."""

import asyncio
import json
from dataclasses import asdict

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionFactory
from app.models.source import Source
from app.modules.crawling.infrastructure.egress import CrawlEgressPolicy
from app.modules.crawling.infrastructure.http_transport import SafeHttpTransport
from app.modules.crawling.infrastructure.source_capability_detector import (
    SourceCapabilityDetector,
)
from app.modules.crawling.infrastructure.source_profile_registry import SourceProfileRegistry

SMOKE_TITLES = {"ایرنا", "شرق", "اکونومیست - بریتانیا", "گاردین - بریتانیا"}


async def _run() -> dict[str, object]:
    registry = SourceProfileRegistry()
    async with SessionFactory() as session:
        sources = list((await session.scalars(select(Source).order_by(Source.title))).all())

    audit = []
    for source in sources:
        profile = registry.resolve(source.base_url)
        audit.append(
            {
                "id": str(source.id),
                "title": source.title,
                "base_url": source.base_url,
                "active": source.is_active,
                "profile_key": profile.key,
                "declared_methods": [method.value for method in profile.methods],
                "requires_browser": profile.requires_browser,
                "content_restricted": profile.content_restricted,
                "remote_egress_required": profile.remote_egress_required,
            }
        )

    transport = SafeHttpTransport(
        user_agent=settings.crawler_user_agent,
        timeout_ms=min(settings.crawler_page_timeout_ms, 20000),
        egress=CrawlEgressPolicy.from_environment(),
    )
    detector = SourceCapabilityDetector()
    selected = [source for source in sources if source.title in SMOKE_TITLES]
    snapshots = await asyncio.gather(
        *(detector.detect(source.base_url, transport.get) for source in selected)
    )
    smoke = [
        {"title": source.title, **asdict(snapshot)}
        for source, snapshot in zip(selected, snapshots, strict=True)
    ]
    return {
        "phase": 2,
        "catalog_total": len(audit),
        "catalog_active": sum(item["active"] for item in audit),
        "profiled_active": sum(item["active"] and item["profile_key"] != "generic" for item in audit),
        "audit": audit,
        "smoke": smoke,
    }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(_run()), ensure_ascii=False, indent=2))
