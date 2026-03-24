import json
from datetime import datetime
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_state() -> dict[str, Any]:
    return {
        "schema": 1,
        "updated_at": _now_iso(),
        "dates": {},
        "failed_dates": {},
    }


def load_all_odds_score_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _default_state()

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_state()
        data.setdefault("schema", 1)
        data.setdefault("updated_at", _now_iso())
        data.setdefault("dates", {})
        data.setdefault("failed_dates", {})
        return data
    except Exception:
        return _default_state()


def save_all_odds_score_state(path: Path, state: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    state = state if isinstance(state, dict) else _default_state()
    state["updated_at"] = _now_iso()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    return path


def get_day_state(state: dict[str, Any], date_iso: str) -> dict[str, Any] | None:
    dates = state.get("dates") if isinstance(state, dict) else {}
    if not isinstance(dates, dict):
        return None
    row = dates.get(date_iso)
    return row if isinstance(row, dict) else None


def mark_day_state(
    state: dict[str, Any],
    date_iso: str,
    *,
    match_count: int,
    scored_count: int,
    pending_eligible_count: int,
    future_blocked_count: int,
    status: str,
    count_changed: bool,
    previous_match_count: int | None = None,
) -> dict[str, Any]:
    state = state if isinstance(state, dict) else _default_state()
    dates = state.get("dates")
    failed = state.get("failed_dates")
    if not isinstance(dates, dict):
        dates = {}
    if not isinstance(failed, dict):
        failed = {}

    dates[date_iso] = {
        "status": str(status or ""),
        "match_count": int(match_count),
        "scored_count": int(scored_count),
        "pending_eligible_count": int(pending_eligible_count),
        "future_blocked_count": int(future_blocked_count),
        "count_changed": bool(count_changed),
        "previous_match_count": (
            int(previous_match_count) if previous_match_count is not None else None
        ),
        "updated_at": _now_iso(),
    }
    if date_iso in failed:
        failed.pop(date_iso, None)

    state["dates"] = dates
    state["failed_dates"] = failed
    state["updated_at"] = _now_iso()
    return state


def mark_day_failure(
    state: dict[str, Any], date_iso: str, error_message: str
) -> dict[str, Any]:
    state = state if isinstance(state, dict) else _default_state()
    failed = state.get("failed_dates")
    if not isinstance(failed, dict):
        failed = {}

    failed[date_iso] = {
        "status": "failed",
        "error": str(error_message or ""),
        "updated_at": _now_iso(),
    }
    state["failed_dates"] = failed
    state["updated_at"] = _now_iso()
    return state
