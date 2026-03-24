from datetime import datetime

from utils.all_odds_store import merge_all_odds, summarize_score_progress


def test_merge_all_odds_preserves_existing_scores_when_snapshot_is_empty():
    existing = {
        "schema": 1,
        "date": "2026-03-18",
        "matches": {
            "abc123": {
                "match_id": "abc123",
                "time": "18:45",
                "url": "https://www.flashscore.com/match/football/a/b/?mid=abc123",
                "home": "Alpha",
                "away": "Beta",
                "status": "final",
                "odds": {"1": "1.60", "X": "4.10", "2": "5.20"},
                "scores": {
                    "ft_home": 2,
                    "ft_away": 1,
                    "1h_home": 1,
                    "1h_away": 0,
                },
            }
        },
    }
    snapshot = {
        "schema": 1,
        "date": "2026-03-18",
        "matches": {
            "abc123": {
                "match_id": "abc123",
                "time": "18:45",
                "url": "https://www.flashscore.com/match/football/a/b/?mid=abc123",
                "home": "Alpha",
                "away": "Beta",
                "status": "final",
                "odds": {"1": "1.60", "X": "4.10", "2": "5.20"},
                "scores": {},
            }
        },
    }

    merged, stats = merge_all_odds(existing, snapshot)

    assert stats.updated == 0
    assert merged["matches"]["abc123"]["scores"]["ft_home"] == 2
    assert merged["matches"]["abc123"]["scores"]["ft_away"] == 1
    assert merged["matches"]["abc123"]["scores"]["1h_home"] == 1


def test_summarize_score_progress_uses_grace_period_for_old_days():
    payload = {
        "schema": 1,
        "date": "2026-03-18",
        "matches": {
            "done": {
                "status": "final",
                "scores": {"ft_home": 2, "ft_away": 1},
            },
            "stale_missing": {
                "status": "scheduled",
            },
        },
    }

    summary = summarize_score_progress(
        payload,
        now=datetime(2026, 3, 24, 12, 0, 0),
        completion_grace_days=5,
    )

    assert summary["match_count"] == 2
    assert summary["scored_count"] == 1
    assert summary["pending_eligible_count"] == 1
    assert summary["future_blocked_count"] == 0
    assert summary["status"] == "complete"


def test_summarize_score_progress_marks_future_days_as_future():
    payload = {
        "schema": 1,
        "date": "2026-03-26",
        "matches": {
            "future_1": {
                "status": "scheduled",
            },
            "future_2": {
                "status": "scheduled",
            },
        },
    }

    summary = summarize_score_progress(
        payload,
        now=datetime(2026, 3, 24, 12, 0, 0),
    )

    assert summary["match_count"] == 2
    assert summary["scored_count"] == 0
    assert summary["pending_eligible_count"] == 0
    assert summary["future_blocked_count"] == 2
    assert summary["status"] == "future"
