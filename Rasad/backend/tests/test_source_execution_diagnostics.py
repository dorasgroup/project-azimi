from app.modules.monitoring.domain import (
    SourceExecutionDiagnoser,
    SourceExecutionFacts,
)


def facts(**overrides: object) -> SourceExecutionFacts:
    values: dict[str, object] = {
        "crawl_status": "success",
        "crawl_reason_code": "completed",
        "crawl_summary": "crawl completed",
        "discovered_total": 20,
        "selected_total": 5,
        "fetched_total": 5,
        "successful_pages": 5,
        "provider_counts": {"rss": 10, "sitemap": 10},
        "selected_provider_counts": {"rss": 3, "sitemap": 2},
        "prepared_documents": 0,
        "rejection_reasons": (),
        "duplicate_count": 0,
    }
    values.update(overrides)
    return SourceExecutionFacts(**values)  # type: ignore[arg-type]


def test_diagnoses_no_topic_match_after_successful_fetch() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(
        facts(
            rejection_reasons=(
                "Content relevance score is below the configured threshold",
            )
        )
    )

    assert diagnostic.status == "empty"
    assert diagnostic.reason_code == "no_topic_match"
    assert diagnostic.crawl_reason_code == "completed"
    assert diagnostic.rejection_counts == {"no_topic_match": 1}
    assert diagnostic.stage_counts["fetched"] == 5


def test_diagnoses_dominant_date_rejection_deterministically() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(
        facts(
            rejection_reasons=(
                "Publication date is before the requested date range",
                "Publication date is after the requested date range",
                "Publication date is missing; requested date range cannot be verified",
            )
        )
    )

    assert diagnostic.reason_code == "date_out_of_range"
    assert diagnostic.rejection_counts == {
        "date_out_of_range": 2,
        "missing_publication_date": 1,
    }


def test_crawl_failure_takes_precedence_over_document_preparation() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(
        facts(
            crawl_status="failed",
            crawl_reason_code="probe_failed",
            discovered_total=0,
            selected_total=0,
            fetched_total=0,
            successful_pages=0,
        )
    )

    assert diagnostic.status == "failed"
    assert diagnostic.reason_code == "probe_failed"
    assert "egress" in diagnostic.recommended_action


def test_partial_content_success_preserves_rejection_evidence() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(
        facts(
            prepared_documents=3,
            rejection_reasons=("Extracted content is too short",),
            duplicate_count=1,
        )
    )

    assert diagnostic.status == "partial"
    assert diagnostic.reason_code == "partial_content_success"
    assert diagnostic.stage_counts["prepared"] == 3
    assert diagnostic.stage_counts["duplicates"] == 1
    assert diagnostic.rejection_counts == {"extraction_failed": 1}


def test_duplicate_only_is_not_reported_as_unknown_failure() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(facts(duplicate_count=4))

    assert diagnostic.status == "empty"
    assert diagnostic.reason_code == "duplicate_only"
    assert diagnostic.stage_counts["duplicates"] == 4


def test_complete_source_serializes_bounded_stage_and_provider_counts() -> None:
    diagnostic = SourceExecutionDiagnoser.diagnose(facts(prepared_documents=5))

    assert diagnostic.reason_code == "completed"
    payload = diagnostic.as_dict()
    assert payload["stage_counts"] == {
        "discovered": 20,
        "selected": 5,
        "fetched": 5,
        "fetch_succeeded": 5,
        "prepared": 5,
        "rejected": 0,
        "duplicates": 0,
    }
    assert payload["provider_counts"] == {"rss": 10, "sitemap": 10}
