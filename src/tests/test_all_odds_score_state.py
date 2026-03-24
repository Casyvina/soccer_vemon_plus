from datetime import datetime

from utils.all_odds_score_state import list_recheck_candidates


def test_list_recheck_candidates_selects_pending_and_incomplete_dates():
    state = {
        "schema": 1,
        "dates": {
            "2026-03-21": {
                "status": "incomplete",
                "match_count": 10,
                "updated_at": "2026-03-24T05:30:33",
            },
            "2026-03-24": {
                "status": "pending",
                "match_count": 170,
                "updated_at": "2026-03-24T06:10:00",
            },
            "2026-03-20": {
                "status": "complete",
                "match_count": 288,
                "updated_at": "2026-03-24T04:38:27",
            },
            "2026-03-30": {
                "status": "future",
                "match_count": 50,
                "updated_at": "2026-03-24T06:15:00",
            },
        },
        "failed_dates": {},
    }

    candidates = list_recheck_candidates(
        state,
        now=datetime(2026, 3, 24, 12, 0, 0),
    )

    assert [item["date_iso"] for item in candidates] == [
        "2026-03-21",
        "2026-03-24",
    ]
    assert [item["day_offset"] for item in candidates] == [-3, 0]


def test_list_recheck_candidates_can_include_failed_dates():
    state = {
        "schema": 1,
        "dates": {},
        "failed_dates": {
            "2026-03-23": {
                "status": "failed",
                "error": "timeout",
                "updated_at": "2026-03-24T05:00:00",
            }
        },
    }

    candidates = list_recheck_candidates(
        state,
        now=datetime(2026, 3, 24, 12, 0, 0),
        include_failed=True,
    )

    assert len(candidates) == 1
    assert candidates[0]["date_iso"] == "2026-03-23"
    assert candidates[0]["day_offset"] == -1
    assert candidates[0]["status"] == "failed"


def test_list_recheck_candidates_honors_range_and_limit():
    state = {
        "schema": 1,
        "dates": {
            "2026-03-14": {
                "status": "incomplete",
                "match_count": 2,
                "updated_at": "2026-03-24T01:00:00",
            },
            "2026-03-22": {
                "status": "pending",
                "match_count": 5,
                "updated_at": "2026-03-24T02:00:00",
            },
            "2026-03-24": {
                "status": "pending",
                "match_count": 7,
                "updated_at": "2026-03-24T03:00:00",
            },
        },
        "failed_dates": {},
    }

    candidates = list_recheck_candidates(
        state,
        now=datetime(2026, 3, 24, 12, 0, 0),
        min_day_offset=-2,
        max_day_offset=0,
        limit=1,
    )

    assert len(candidates) == 1
    assert candidates[0]["date_iso"] == "2026-03-22"
