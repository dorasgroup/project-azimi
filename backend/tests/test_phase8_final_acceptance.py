from scripts.verify_phase8_final_acceptance import verify


def test_phase8_final_acceptance_is_offline_and_deterministic() -> None:
    result = verify()

    assert result["status"] == "passed"
    assert result["provider_diversity_preserved"] is True
    assert result["known_out_of_range_rejected"] is True
    assert result["unknown_date_preserved_for_fetch"] is True
    assert result["deterministic_order"] is True
    assert result["canonical_topic_preserved"] is True
    assert result["network_calls"] == 0
    assert result["database_writes"] == 0
    assert result["analysis_provider_calls"] == 0
