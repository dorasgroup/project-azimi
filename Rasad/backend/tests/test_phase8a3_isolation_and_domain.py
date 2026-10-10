from datetime import UTC, datetime
from uuid import uuid4

import pytest

from scripts.phase8a3_isolation import assert_isolated
from app.modules.analysis.domain.source_analysis import SourceAnalysis, SourceAnalysisStatus
from app.modules.analysis.domain.run_aggregation import RunAggregation, RunAggregationStatus


DB = "postgresql+asyncpg://phase8:synthetic@rasad-phase8-pg-LetbR0:5432/phase8a3_lifecycle"
REDIS = "redis://rasad-phase8-redis-LetbR0:6379/8"


def test_isolation_allowlist_refuses_production_and_ambiguous_endpoints_before_io():
    assert_isolated(DB,REDIS)
    for db,broker in [
        ("postgresql+asyncpg://user:synthetic@postgres:5432/rasad",REDIS),
        (DB.replace("phase8a3_lifecycle","rasad"),REDIS),
        (DB, "redis://rasad-redis:6379/0"),
        (DB,REDIS.replace("/8","/0")),
        (DB+"?host=postgres",REDIS),
        (DB.replace("rasad-phase8-pg-LetbR0","5.10.249.72"),REDIS),
    ]:
        with pytest.raises(RuntimeError):
            assert_isolated(db,broker)


def test_analysis_cancel_preserves_history_and_is_idempotent():
    now = datetime.now(UTC)
    a = SourceAnalysis(uuid4(),uuid4(),uuid4(),uuid4(),status=SourceAnalysisStatus.RUNNING,
                       claim_token=uuid4(),lease_expires_at=now,attempt_count=3,error_code="OLD",input_digest="a"*64)
    assert a.cancel(now)
    assert not a.cancel(now)
    assert a.status == SourceAnalysisStatus.CANCELLED and a.claim_token is None and a.lease_expires_at is None
    assert (a.attempt_count,a.error_code,a.input_digest,a.finished_at)==(3,"OLD","a"*64,now)
    a.status = SourceAnalysisStatus.COMPLETED
    assert not a.cancel(now)


def test_aggregation_cancel_preserves_manifest_and_terminal_history():
    now = datetime.now(UTC)
    a = RunAggregation(uuid4(),uuid4(),RunAggregationStatus.RUNNING,"all_terminal",None,"a"*64,"b"*64,
                       3,now,now,uuid4(),now,now,None,now,"OLD","synthetic")
    cancelled = a.cancel(now)
    assert cancelled.status == RunAggregationStatus.CANCELLED
    assert cancelled.claim_token is None and cancelled.lease_expires_at is None
    assert (cancelled.manifest_digest,cancelled.attempt_count,cancelled.error_code)==("a"*64,3,"OLD")
    assert cancelled.cancel(now) is cancelled
