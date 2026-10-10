"""Deterministic acceptance matrix for phase-six source diagnostics."""

import json

from app.modules.monitoring.domain import SourceExecutionDiagnoser, SourceExecutionFacts


def _facts(
    *,
    crawl_reason: str = "completed",
    prepared: int = 0,
    rejected: tuple[str, ...] = (),
    duplicates: int = 0,
) -> SourceExecutionFacts:
    return SourceExecutionFacts(
        crawl_status="success" if crawl_reason == "completed" else "empty",
        crawl_reason_code=crawl_reason,
        crawl_summary="bounded fixture",
        discovered_total=12,
        selected_total=4,
        fetched_total=4,
        successful_pages=4,
        provider_counts={"rss": 5, "sitemap": 4, "gdelt": 3},
        selected_provider_counts={"rss": 2, "sitemap": 1, "gdelt": 1},
        prepared_documents=prepared,
        rejection_reasons=rejected,
        duplicate_count=duplicates,
    )


def verify() -> dict[str, object]:
    fixtures = {
        "complete": _facts(prepared=4),
        "topic_rejected": _facts(
            rejected=("Content relevance score is below the configured threshold",) * 4
        ),
        "date_rejected": _facts(
            rejected=("Publication date is before the requested date range",) * 4
        ),
        "missing_date": _facts(
            rejected=(
                "Publication date is missing; requested date range cannot be verified",
            )
            * 4
        ),
        "extraction_failed": _facts(rejected=("Extracted content is too short",) * 4),
        "duplicate_only": _facts(duplicates=4),
        "discovery_empty": _facts(crawl_reason="no_urls_discovered"),
    }
    matrix = {
        name: SourceExecutionDiagnoser.diagnose(item).reason_code
        for name, item in fixtures.items()
    }
    expected = {
        "complete": "completed",
        "topic_rejected": "no_topic_match",
        "date_rejected": "date_out_of_range",
        "missing_date": "missing_publication_date",
        "extraction_failed": "extraction_failed",
        "duplicate_only": "duplicate_only",
        "discovery_empty": "no_urls_discovered",
    }
    assert matrix == expected
    return {
        "phase": 6,
        "status": "passed",
        "classified_scenarios": len(matrix),
        "matrix": matrix,
        "public_worker_contract_changed": False,
        "database_migration": False,
        "analysis_provider_calls": 0,
        "network_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
