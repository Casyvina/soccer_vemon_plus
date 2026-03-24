import json
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_RECHECK_STATUSES = ("pending", "incomplete")


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


def list_recheck_candidates(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    statuses: tuple[str, ...] | list[str] | None = None,
    include_failed: bool = False,
    min_day_offset: int = -7,
    max_day_offset: int = 5,
    limit: int = 0,
) -> list[dict[str, Any]]:
    now = now or datetime.now()
    target_statuses = {
        str(value or "").strip().lower()
        for value in (statuses or DEFAULT_RECHECK_STATUSES)
        if str(value or "").strip()
    }

    candidates: list[dict[str, Any]] = []
    seen_dates: set[str] = set()

    dates = state.get("dates") if isinstance(state, dict) else {}
    if isinstance(dates, dict):
        for date_iso, row in dates.items():
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "").strip().lower()
            if target_statuses and status not in target_statuses:
                continue

            day_offset = _resolve_day_offset(date_iso, now)
            if day_offset is None:
                continue
            if day_offset < int(min_day_offset) or day_offset > int(max_day_offset):
                continue

            seen_dates.add(str(date_iso))
            candidates.append(
                {
                    "date_iso": str(date_iso),
                    "day_offset": int(day_offset),
                    "status": status,
                    "match_count": int(row.get("match_count") or 0),
                    "updated_at": str(row.get("updated_at") or ""),
                    "source": "dates",
                }
            )

    if include_failed:
        failed_dates = state.get("failed_dates") if isinstance(state, dict) else {}
        if isinstance(failed_dates, dict):
            for date_iso, row in failed_dates.items():
                if str(date_iso) in seen_dates or not isinstance(row, dict):
                    continue

                day_offset = _resolve_day_offset(date_iso, now)
                if day_offset is None:
                    continue
                if day_offset < int(min_day_offset) or day_offset > int(max_day_offset):
                    continue

                candidates.append(
                    {
                        "date_iso": str(date_iso),
                        "day_offset": int(day_offset),
                        "status": "failed",
                        "match_count": 0,
                        "updated_at": str(row.get("updated_at") or ""),
                        "source": "failed_dates",
                    }
                )

    candidates.sort(
        key=lambda item: (
            int(item.get("day_offset") or 0),
            str(item.get("updated_at") or ""),
            str(item.get("date_iso") or ""),
        )
    )
    if int(limit) > 0:
        return candidates[: int(limit)]
    return candidates


def _resolve_day_offset(date_iso: str, now: datetime) -> int | None:
    text = str(date_iso or "").strip()
    if not text:
        return None

    try:
        target = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None

    return (target - now.date()).days
