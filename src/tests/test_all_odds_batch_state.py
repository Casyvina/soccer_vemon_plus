from utils.all_odds_store import (
    begin_details_batch_in_payload,
    finish_details_batch_in_payload,
    list_detail_candidates,
    mark_details_failed_in_payload,
    mark_details_fetched_in_payload,
    start_details_attempt_in_payload,
    update_details_batch_progress_in_payload,
)


def _sample_payload() -> dict:
    return {
        "schema": 1,
        "date": "2026-03-24",
        "matches": {
            "match_a": {
                "match_id": "match_a",
                "url": "https://www.flashscore.com/match/football/a/b/?mid=match_a",
                "details_fetched": False,
                "details_attempt_count": 0,
                "details_last_status": "pending",
            },
            "match_b": {
                "match_id": "match_b",
                "url": "https://www.flashscore.com/match/football/c/d/?mid=match_b",
                "details_fetched": False,
                "details_attempt_count": 2,
                "details_last_status": "failed",
            },
            "match_c": {
                "match_id": "match_c",
                "url": "https://www.flashscore.com/match/football/e/f/?mid=match_c",
                "details_fetched": True,
                "details_attempt_count": 1,
                "details_last_status": "success",
            },
        },
    }


def test_list_detail_candidates_filters_attempts_and_failures():
    payload = _sample_payload()

    default_candidates = list_detail_candidates(payload, max_attempts=3)
    assert [item["match_id"] for item in default_candidates] == ["match_a", "match_b"]

    only_failed = list_detail_candidates(payload, only_failed=True, max_attempts=3)
    assert [item["match_id"] for item in only_failed] == ["match_b"]

    capped = list_detail_candidates(payload, max_attempts=2)
    assert [item["match_id"] for item in capped] == ["match_a"]


def test_attempt_failure_success_and_batch_progress():
    payload = _sample_payload()

    assert start_details_attempt_in_payload(payload, "match_a") is True
    assert payload["matches"]["match_a"]["details_attempt_count"] == 1
    assert payload["matches"]["match_a"]["details_last_status"] == "running"

    assert mark_details_failed_in_payload(payload, "match_a", "timeout") is True
    assert payload["matches"]["match_a"]["details_last_status"] == "failed"
    assert payload["matches"]["match_a"]["details_last_error"] == "timeout"

    assert mark_details_fetched_in_payload(payload, "match_a", True) is True
    assert payload["matches"]["match_a"]["details_fetched"] is True
    assert payload["matches"]["match_a"]["details_last_status"] == "success"
    assert payload["matches"]["match_a"]["details_last_error"] == ""

    assert begin_details_batch_in_payload(
        payload,
        source="test",
        planned_count=2,
        only_failed=False,
        include_fetched=False,
        max_attempts=3,
    ) is True
    assert update_details_batch_progress_in_payload(
        payload,
        processed_count=1,
        success_count=1,
        failure_count=0,
        remaining_count=1,
        current_match_id="match_b",
        last_completed_match_id="match_a",
    ) is True
    assert payload["details_batch"]["current_match_id"] == "match_b"
    assert payload["details_batch"]["last_completed_match_id"] == "match_a"

    assert finish_details_batch_in_payload(
        payload,
        processed_count=2,
        success_count=1,
        failure_count=1,
        remaining_count=0,
    ) is True
    assert payload["details_batch"]["status"] == "completed_with_failures"
    assert payload["details_batch"]["finished_at"] is not None
    assert payload["details_batch"]["last_completed_match_id"] == "match_a"
