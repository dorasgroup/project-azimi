"""Application use cases for source analysis execution."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
from typing import Sequence
from uuid import UUID

from app.modules.analysis.domain.source_analysis import (
    SOURCE_ANALYSIS_HEARTBEAT_SECONDS,
    AnalysisClaimStatus,
    SourceAnalysisEvidence,
    SourceAnalysisResultData,
    SourceAnalysisSpec,
    TransientAnalysisError,
    classify_analysis_error,
)
from app.modules.analysis.ports.run_aggregation import (
    SourceAnalysisTerminalPublisherPort,
)
from app.modules.analysis.ports.source_analysis import (
    SourceAnalysisRepositoryPort,
    SourceAnalysisResultRepositoryPort,
    SourceAnalyzerPort,
    SourceTaskDocumentReaderPort,
    SourceTaskDocumentRecord,
)

logger = logging.getLogger("rasad.analysis.source_analysis")


class AnalyzeSourceUseCase:
    """Core domain use case: analyze a single source's snapshot documents."""

    def __init__(self, analyzer: SourceAnalyzerPort) -> None:
        self._analyzer = analyzer

    async def execute(
        self,
        spec: SourceAnalysisSpec,
        documents: Sequence[SourceTaskDocumentRecord],
        topic: str,
    ) -> SourceAnalysisResultData:
        return await self._analyzer.analyze_source(spec, documents, topic)


class ExecuteSourceAnalysisUseCase:
    """Orchestrates distributed execution of source analysis: claim, heartbeat, analysis, fenced persistence."""

    def __init__(
        self,
        analysis_repository: SourceAnalysisRepositoryPort,
        result_repository: SourceAnalysisResultRepositoryPort,
        document_reader: SourceTaskDocumentReaderPort,
        analyze_use_case: AnalyzeSourceUseCase,
        heartbeat_seconds: int = SOURCE_ANALYSIS_HEARTBEAT_SECONDS,
        terminal_publisher: SourceAnalysisTerminalPublisherPort | None = None,
    ) -> None:
        self._analysis_repo = analysis_repository
        self._result_repo = result_repository
        self._doc_reader = document_reader
        self._analyze_use_case = analyze_use_case
        self._heartbeat_seconds = heartbeat_seconds
        self._terminal_publisher = terminal_publisher

    async def execute(self, analysis_id: UUID | str) -> dict[str, object]:
        if isinstance(analysis_id, str):
            analysis_id = UUID(analysis_id)

        outcome = await self._analysis_repo.claim_explicit(analysis_id)
        if outcome.status != AnalysisClaimStatus.CLAIMED:
            logger.info("Analysis %s not claimed: %s", analysis_id, outcome.status)
            if (outcome.status == AnalysisClaimStatus.ALREADY_TERMINAL
                and outcome.current_status != "cancelled" and self._terminal_publisher is not None):
                try:
                    analysis = await self._analysis_repo.get_by_id(analysis_id)
                    if analysis:
                        self._terminal_publisher.publish_source_analysis_terminal(
                            run_id=analysis.run_id,
                            source_analysis_id=analysis.id,
                            outcome=analysis.status.value,
                            occurred_at=datetime.now(UTC),
                        )
                except Exception as pub_err:
                    logger.warning("Failed to republish analysis terminal on already_terminal: %s", pub_err)

            res: dict[str, object] = {
                "status": outcome.status.value,
                "analysis_id": str(analysis_id),
            }
            if outcome.lease_expires_at is not None:
                res["lease_expires_at"] = outcome.lease_expires_at.isoformat()
            return res

        claim_token = outcome.claim_token
        assert claim_token is not None

        analysis = await self._analysis_repo.get_by_id(analysis_id)
        if analysis is None:
            return {"status": "not_found", "analysis_id": str(analysis_id)}

        # Heartbeat loop
        heartbeat_stop = asyncio.Event()
        heartbeat_lost = asyncio.Event()

        async def _heartbeat_loop() -> None:
            while not heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(heartbeat_stop.wait(), timeout=self._heartbeat_seconds)
                    break
                except asyncio.TimeoutError:
                    try:
                        renewed = await self._analysis_repo.renew_lease(analysis_id, claim_token)
                        if not renewed:
                            logger.warning("Heartbeat failed to renew lease for analysis %s", analysis_id)
                            heartbeat_lost.set()
                            break
                    except Exception as e:
                        logger.warning("Error during analysis heartbeat: %s", e)

        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            # 1. Load immutable snapshot documents
            documents = await self._doc_reader.get_documents_for_task(analysis.source_task_id)

            # 2. Invoke cognitive analysis
            result_data = await self._analyze_use_case.execute(
                spec=analysis.analysis_spec,
                documents=documents,
                topic=analysis.analysis_spec.topic,
            )

            # Stop heartbeat before terminalizing
            heartbeat_stop.set()
            await heartbeat_task

            if heartbeat_lost.is_set():
                logger.warning("Lost ownership during analysis %s; discarding results", analysis_id)
                return {"status": "ownership_lost", "analysis_id": str(analysis_id)}

            # 3. Persist result with side-effect fence
            saved = await self._result_repo.save_result_fenced(analysis_id, claim_token, result_data)
            if not saved:
                logger.warning("Fenced persistence rejected for analysis %s", analysis_id)
                return {"status": "fencing_rejected", "analysis_id": str(analysis_id)}

            # 4. Atomically complete analysis
            completed = await self._analysis_repo.complete_analysis(
                analysis_id, claim_token, input_digest=result_data.input_digest
            )
            if not completed:
                logger.warning("Failed to mark analysis %s as completed", analysis_id)
                return {"status": "completion_failed", "analysis_id": str(analysis_id)}

            logger.info("Successfully completed source analysis %s", analysis_id)

            if self._terminal_publisher is not None:
                try:
                    self._terminal_publisher.publish_source_analysis_terminal(
                        run_id=analysis.run_id,
                        source_analysis_id=analysis.id,
                        outcome="completed",
                        occurred_at=datetime.now(UTC),
                    )
                except Exception as pub_err:
                    logger.warning("Failed to publish analysis terminal event: %s", pub_err)

            return {"status": "completed", "analysis_id": str(analysis_id)}

        except Exception as raw_exc:
            heartbeat_stop.set()
            await heartbeat_task

            classified = classify_analysis_error(raw_exc)
            if isinstance(classified, TransientAnalysisError) and analysis.attempt_count < 3:
                logger.warning(
                    "Transient failure in analysis %s (%s); releasing claim for retry",
                    analysis_id,
                    type(raw_exc).__name__,
                )
                await self._analysis_repo.release_claim(analysis_id, claim_token)
                raise classified

            # Permanent failure
            logger.error("Terminal source analysis failure %s (%s)", analysis_id, type(raw_exc).__name__)
            failed = await self._analysis_repo.fail_analysis(
                analysis_id,
                claim_token,
                error_code="ANALYSIS_RETRY_EXHAUSTED" if isinstance(classified, TransientAnalysisError) else type(raw_exc).__name__,
                error_message="Source analysis did not produce a valid result",
            )

            if failed and self._terminal_publisher is not None:
                try:
                    self._terminal_publisher.publish_source_analysis_terminal(
                        run_id=analysis.run_id,
                        source_analysis_id=analysis.id,
                        outcome="failed",
                        occurred_at=datetime.now(UTC),
                    )
                except Exception as pub_err:
                    logger.warning("Failed to publish analysis terminal event on failure: %s", pub_err)

            return {
                "status": "failed",
                "analysis_id": str(analysis_id),
                "error": "Source analysis did not produce a valid result",
            }
