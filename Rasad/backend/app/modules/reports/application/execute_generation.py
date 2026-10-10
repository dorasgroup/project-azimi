"""Durable report orchestration; synchronous rendering runs off the heartbeat loop."""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.modules.reports.application.document_snapshot import build_document_snapshot
from app.modules.reports.domain.contracts import ReportExportFormat, ReportExportRequest
from app.modules.reports.domain.generation import ReportClaimKind
from app.modules.reports.ports import ReportRenderer
from app.modules.reports.ports.generation import ReportArtifactStore, ReportGenerationRepository

REPORT_LEASE_SECONDS = 120
REPORT_HEARTBEAT_SECONDS = 10


class ExecuteReportGeneration:
    def __init__(self, repository: ReportGenerationRepository, renderer: ReportRenderer, storage: ReportArtifactStore, *, lease_seconds: int = REPORT_LEASE_SECONDS, heartbeat_seconds: float = REPORT_HEARTBEAT_SECONDS) -> None:
        if heartbeat_seconds <= 0 or lease_seconds <= 2 * heartbeat_seconds:
            raise ValueError("Report heartbeat must be well below lease duration")
        self._repository, self._renderer, self._storage = repository, renderer, storage
        self._lease_seconds, self._heartbeat_seconds = lease_seconds, heartbeat_seconds

    async def execute(self, generation_id: UUID) -> dict[str, object]:
        token = uuid4()
        claim = await self._repository.claim(generation_id, token, self._lease_seconds, datetime.now(UTC))
        if claim.kind != ReportClaimKind.CLAIMED:
            return {"status": claim.kind.value, "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None}
        stop = asyncio.Event()
        lost = False

        async def heartbeat() -> None:
            nonlocal lost
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self._heartbeat_seconds)
                except TimeoutError:
                    try:
                        if not await self._repository.renew_lease(generation_id, token, self._lease_seconds, datetime.now(UTC)):
                            lost = True
                            return
                    except Exception:
                        lost = True
                        return

        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            source = await self._repository.load_input(generation_id, token)
            if source is None:
                return {"status": "claim_lost"}
            try:
                document = build_document_snapshot(source)
            except (ValueError, TypeError, KeyError):
                failed = await self._repository.fail_permanently(generation_id, token, "InvalidImmutableReportInput")
                return {"status": "failed" if failed else "claim_lost"}
            metadata = await self._repository.artifact_metadata(generation_id, token)
            paths: dict[str, str] = {}
            for fmt in source.spec.formats:
                if lost:
                    return {"status": "claim_lost"}
                existing = metadata.get(fmt)
                if existing is None or not await asyncio.to_thread(self._storage.verify, generation_id, fmt, *existing):
                    rendered = await asyncio.to_thread(
                        self._renderer.render,
                        ReportExportRequest(document, ReportExportFormat(fmt)),
                    )
                    if not rendered.content:
                        raise ValueError("Renderer produced an empty artifact")
                    if lost:
                        return {"status": "claim_lost"}
                    existing = await asyncio.to_thread(self._storage.save_content_addressed, generation_id, fmt, rendered.content)
                    accepted = await self._repository.accept_artifact(
                        generation_id, token, export_format=fmt, storage_key=existing[0],
                        checksum=existing[1], size=existing[2],
                        schema_version=source.spec.schema_version, template_version=source.spec.template_version,
                    )
                    if not accepted:
                        return {"status": "claim_lost"}
                paths[fmt] = str(self._storage.path_for(generation_id, fmt, existing[0])).replace("\\", "/")
            if lost:
                return {"status": "claim_lost"}
            completed = await self._repository.complete(
                generation_id, token, paths,
                full_report=document.full_report, sources=document.sources_json,
            )
            return {"status": "completed" if completed else "claim_lost"}
        except (ValueError, TypeError, KeyError):
            failed = await self._repository.fail_permanently(generation_id, token, "PermanentReportRenderError")
            return {"status": "failed" if failed else "claim_lost"}
        except Exception:
            await self._repository.release_for_retry(generation_id, token)
            raise
        finally:
            stop.set()
            await heartbeat_task
