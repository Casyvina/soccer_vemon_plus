import json
import math
import numbers
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse, parse_qs


DAY_CODE_MAP = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}

SCORE_TERMINAL_STATUSES = {
    "abandoned",
    "awarded",
    "cancelled",
    "canceled",
    "final",
    "postponed",
    "walkover",
    "wo",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def infer_iso_date(day_month: str, day_code: str, now: Optional[datetime] = None) -> str:
    """
    Convert Flashscore header date like "04-02" + day code like "WE" to "YYYY-MM-DD".

    Uses weekday matching across candidate years [now.year-1, now.year, now.year+1] to
    handle year boundaries more reliably.
    """
    now = now or datetime.now()
    day_month = (day_month or "").strip()
    day_code = (day_code or "").strip().upper()

    if not re.fullmatch(r"\d{2}-\d{2}", day_month):
        return "unknown"

    target_weekday = DAY_CODE_MAP.get(day_code)
    candidates: list[tuple[int, datetime]] = []
    for year in (now.year - 1, now.year, now.year + 1):
        try:
            dt = datetime.strptime(f"{day_month}-{year}", "%d-%m-%Y")
        except ValueError:
            continue
        candidates.append((year, dt))

    if not candidates:
        return "unknown"

    if target_weekday is None:
        # no weekday to validate against -> choose closest to now
        _, best = min(candidates, key=lambda t: abs((t[1] - now).days))
        return best.strftime("%Y-%m-%d")

    matching = [dt for _, dt in candidates if dt.weekday() == target_weekday]
    if matching:
        best = min(matching, key=lambda dt: abs((dt - now).days))
        return best.strftime("%Y-%m-%d")

    # weekday mismatch: still return closest to now (better than failing)
    _, best = min(candidates, key=lambda t: abs((t[1] - now).days))
    return best.strftime("%Y-%m-%d")


def extract_match_id(url: str) -> str:
    """
    Extract a stable match id from Flashscore URLs.
    Prefers `mid` query param when present; otherwise uses the last path segment under `/match/.../`.
    """
    if not url:
        return ""

    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query or "")
        mid = (qs.get("mid") or [""])[0].strip()
        if mid:
            return mid
    except Exception:
        pass

    # Fallback: best-effort extract from path
    m = re.search(r"/match/[^/]+/([^/]+)/", url)
    if m:
        return m.group(1)

    # Final fallback: last non-empty segment
    try:
        parts = [p for p in urlparse(url).path.split("/") if p]
        return parts[-1] if parts else ""
    except Exception:
        return ""


def _atomic_replace(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        src.replace(dest)
    except Exception:
        # Windows: Path.replace uses os.replace internally; fallback to os.replace semantics
        import os

        os.replace(str(src), str(dest))


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        # Accept both UTF-8 and UTF-8 BOM encoded JSON files.
        with open(path, "r", encoding="utf-8-sig") as f:
            return sanitize_for_json(json.load(f) or {})
    except Exception:
        return {}


def sanitize_for_json(value: Any) -> Any:
    """
    Recursively convert a Python object into a JSON-compliant structure:
    - NaN/Infinity -> None
    - numpy/pandas scalars -> native Python types
    - non-serializable objects -> string fallback
    """
    if value is None:
        return None
    if isinstance(value, (str, bool)):
        return value
    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)

    if isinstance(value, numbers.Real):
        try:
            fv = float(value)
        except Exception:
            return None
        if not math.isfinite(fv):
            return None
        return fv

    if isinstance(value, dict):
        return {str(k): sanitize_for_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(v) for v in value]

    item_fn = getattr(value, "item", None)
    if callable(item_fn):
        try:
            return sanitize_for_json(item_fn())
        except Exception:
            return None

    try:
        json.dumps(value, allow_nan=False)
        return value
    except Exception:
        return str(value)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    payload = sanitize_for_json(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp_{path.stem}_{int(time.time()*1000)}{path.suffix}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, allow_nan=False)
    _atomic_replace(tmp, path)


@dataclass
class MergeStats:
    added: int = 0
    updated: int = 0
    unchanged: int = 0


def merge_all_odds(existing: dict[str, Any], snapshot: dict[str, Any]) -> tuple[dict[str, Any], MergeStats]:
    """
    Merge a new ALL_ODDS snapshot into an existing per-date json store.
    Keys are match ids.
    """
    stats = MergeStats()
    merged = dict(existing) if existing else {}
    merged.setdefault("schema", 1)
    merged.setdefault("matches", {})
    merged.setdefault("date", snapshot.get("date", "unknown"))
    merged["updated_at"] = _now_iso()

    matches: dict[str, Any] = merged["matches"] or {}
    for match_id, item in (snapshot.get("matches") or {}).items():
        if not match_id:
            continue

        prev = matches.get(match_id)
        if not prev:
            stats.added += 1
            item = dict(item)
            score_values = item.pop("scores", None)
            item.setdefault("first_seen_at", merged["updated_at"])
            item["last_seen_at"] = merged["updated_at"]
            item.setdefault("details_fetched", False)
            item.setdefault("details_attempt_count", 0)
            item.setdefault("details_last_status", "pending")
            _upsert_scores_in_match(
                item,
                score_values,
                updated_at=merged["updated_at"],
            )
            matches[match_id] = item
            continue

        # update if something changed
        changed = False
        for key in (
            "time",
            "url",
            "home",
            "away",
            "odds",
            "status",
            "competition",
            "country",
        ):
            if item.get(key) != prev.get(key):
                prev[key] = item.get(key)
                changed = True

        if _upsert_scores_in_match(
            prev,
            item.get("scores"),
            updated_at=merged["updated_at"],
        ):
            changed = True

        prev["last_seen_at"] = merged["updated_at"]
        prev.setdefault("details_fetched", False)
        prev.setdefault("details_attempt_count", 0)
        prev.setdefault("details_last_status", "pending")
        if changed:
            prev["updated_at"] = merged["updated_at"]
            stats.updated += 1
        else:
            stats.unchanged += 1

    merged["matches"] = matches
    merged["counts"] = {
        "total": len(matches),
        "added_last_merge": stats.added,
        "updated_last_merge": stats.updated,
    }
    return merged, stats


def build_snapshot_from_dataframe(df, date_iso: str) -> dict[str, Any]:
    """
    Build a per-date ALL_ODDS snapshot dict from the MatchExtractor dataframe.
    Expected columns: Time, Url, Home, Away, Odds1, OddsX, Odds2, Odds1b, OddsXb, Odds2b
    """
    matches: dict[str, Any] = {}

    if df is None or getattr(df, "empty", False):
        return {"schema": 1, "date": date_iso, "matches": {}}

    def _safe_text(v: Any) -> str:
        v = sanitize_for_json(v)
        return "" if v is None else str(v)

    for _, row in df.iterrows():
        url = _safe_text(row.get("Url")).strip()
        match_id = extract_match_id(url)
        if not match_id:
            continue

        odds = sanitize_for_json({
            "1": row.get("Odds1"),
            "X": row.get("OddsX"),
            "2": row.get("Odds2"),
            "1b": row.get("Odds1b"),
            "Xb": row.get("OddsXb"),
            "2b": row.get("Odds2b"),
        })

        matches[match_id] = {
            "match_id": match_id,
            "time": _safe_text(row.get("Time")),
            "url": url,
            "home": _safe_text(row.get("Home")),
            "away": _safe_text(row.get("Away")),
            "odds": odds,
            "details_fetched": False,
            "details_attempt_count": 0,
            "details_last_status": "pending",
        }

    return {"schema": 1, "date": date_iso, "matches": matches}


def mark_details_fetched(path: Path, match_id: str, fetched: bool = True) -> bool:
    """
    Mark a match in a per-day ALL_ODDS json file as `details_fetched`.

    Returns True if the match_id existed and was updated.
    """
    if not match_id:
        return False

    payload = load_json(path)
    matches = payload.get("matches") or {}
    match = matches.get(match_id)
    if not isinstance(match, dict):
        return False

    changed = match.get("details_fetched") is not bool(fetched)
    match["details_fetched"] = bool(fetched)
    if fetched:
        if match.get("details_last_status") != "success":
            changed = True
        match["details_fetched_at"] = _now_iso()
        match["details_last_status"] = "success"
        match["details_last_error"] = ""
        match["details_last_error_at"] = None

    payload["matches"] = matches
    payload["updated_at"] = _now_iso()
    save_json(path, payload)
    return changed


def upsert_scores_in_payload(payload: dict[str, Any], match_id: str, scores: dict[str, Any]) -> bool:
    """
    Upsert a match's scores inside an already-loaded per-day ALL_ODDS payload.

    - Does not overwrite existing values with empty strings / None
    - Returns True only if something changed
    """
    if not match_id or not isinstance(payload, dict):
        return False

    matches = payload.get("matches") or {}
    match = matches.get(match_id)
    if not isinstance(match, dict):
        return False

    existing = match.get("scores")
    if not isinstance(existing, dict):
        existing = {}

    changed = _upsert_scores_in_match(
        match,
        scores,
        updated_at=_now_iso(),
    )
    if not changed:
        return False

    payload["matches"] = matches
    payload["updated_at"] = _now_iso()
    return True


def summarize_score_progress(
    payload: dict[str, Any],
    *,
    now: Optional[datetime] = None,
    completion_grace_days: int = 5,
) -> dict[str, Any]:
    now = now or datetime.now()
    matches = payload.get("matches") if isinstance(payload, dict) else {}
    if not isinstance(matches, dict):
        matches = {}

    date_iso = str(payload.get("date") or "").strip()
    match_dt = _parse_iso_date(date_iso)
    today = now.date()
    is_future = bool(match_dt and match_dt.date() > today)

    match_count = len(matches)
    scored_count = 0
    pending_eligible_count = 0
    future_blocked_count = 0

    for item in matches.values():
        if not isinstance(item, dict):
            continue

        if _has_full_time_scores(item):
            scored_count += 1
            continue

        if is_future:
            future_blocked_count += 1
            continue

        status = _normalize_status(item)
        if status in SCORE_TERMINAL_STATUSES:
            continue

        pending_eligible_count += 1

    status = _derive_score_day_status(
        match_dt=match_dt,
        now=now,
        scored_count=scored_count,
        pending_eligible_count=pending_eligible_count,
        future_blocked_count=future_blocked_count,
        completion_grace_days=max(0, int(completion_grace_days)),
    )

    return {
        "date": date_iso,
        "status": status,
        "match_count": match_count,
        "scored_count": scored_count,
        "pending_eligible_count": pending_eligible_count,
        "future_blocked_count": future_blocked_count,
        "completion_grace_days": max(0, int(completion_grace_days)),
    }


def mark_details_fetched_in_payload(payload: dict[str, Any], match_id: str, fetched: bool = True) -> bool:
    """
    Mark a match as details fetched in an already-loaded per-day ALL_ODDS payload.

    Returns True only if the stored value changed.
    """
    if not match_id or not isinstance(payload, dict):
        return False

    matches = payload.get("matches") or {}
    match = matches.get(match_id)
    if not isinstance(match, dict):
        return False

    fetched = bool(fetched)
    changed = match.get("details_fetched") is not fetched
    match["details_fetched"] = fetched
    if fetched:
        if match.get("details_last_status") != "success":
            changed = True
        match["details_fetched_at"] = _now_iso()
        match["details_last_status"] = "success"
        match["details_last_error"] = ""
        match["details_last_error_at"] = None
    payload["matches"] = matches
    payload["updated_at"] = _now_iso()
    return changed


def list_detail_candidates(
    payload: dict[str, Any],
    *,
    include_fetched: bool = False,
    only_failed: bool = False,
    max_attempts: int = 0,
) -> list[dict[str, Any]]:
    matches = payload.get("matches") if isinstance(payload, dict) else {}
    if not isinstance(matches, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for match_id, item in matches.items():
        if not isinstance(item, dict):
            continue

        if not include_fetched and bool(item.get("details_fetched")):
            continue

        status = str(item.get("details_last_status") or "").strip().lower()
        attempts = _safe_int(item.get("details_attempt_count"))
        if max_attempts > 0 and not bool(item.get("details_fetched")) and attempts >= max_attempts:
            continue

        if only_failed and status != "failed":
            continue

        url = str(item.get("url") or "").strip()
        if not url:
            continue

        candidates.append(
            {
                "match_id": str(match_id),
                "url": url,
                "details_fetched": bool(item.get("details_fetched")),
                "details_attempt_count": attempts,
                "details_last_status": status or "pending",
            }
        )

    return candidates


def list_halftime_score_candidates(
    payload: dict[str, Any],
    *,
    limit: int = 0,
) -> list[dict[str, Any]]:
    """
    Return matches that have full-time scores but are missing half-time scores.

    Each item: {"match_id", "url", "home", "away"}
    limit=0 means no cap.
    """
    matches = payload.get("matches") if isinstance(payload, dict) else {}
    if not isinstance(matches, dict):
        return []

    candidates: list[dict[str, Any]] = []
    for match_id, item in matches.items():
        if not isinstance(item, dict):
            continue

        scores = item.get("scores")
        if not isinstance(scores, dict):
            continue

        # Must have full-time scores
        if (
            _normalize_score_value(scores.get("ft_home")) is None
            or _normalize_score_value(scores.get("ft_away")) is None
        ):
            continue

        # Skip if half-time already present
        if (
            _normalize_score_value(scores.get("1h_home")) is not None
            and _normalize_score_value(scores.get("1h_away")) is not None
        ):
            continue

        url = str(item.get("url") or "").strip()
        if not url:
            continue

        candidates.append(
            {
                "match_id": str(match_id),
                "url": url,
                "home": str(item.get("home_team") or item.get("home") or ""),
                "away": str(item.get("away_team") or item.get("away") or ""),
            }
        )

        if limit > 0 and len(candidates) >= limit:
            break

    return candidates


def start_details_attempt_in_payload(payload: dict[str, Any], match_id: str) -> bool:
    match = _get_match_row(payload, match_id)
    if match is None:
        return False

    match["details_attempt_count"] = _safe_int(match.get("details_attempt_count")) + 1
    match["details_last_status"] = "running"
    match["details_last_attempt_at"] = _now_iso()
    match["details_last_error"] = ""
    match["details_last_error_at"] = None
    payload["updated_at"] = _now_iso()
    return True


def mark_details_failed_in_payload(
    payload: dict[str, Any],
    match_id: str,
    error_message: str,
) -> bool:
    match = _get_match_row(payload, match_id)
    if match is None:
        return False

    match["details_fetched"] = False
    match["details_last_status"] = "failed"
    match["details_last_error"] = str(error_message or "").strip()
    match["details_last_error_at"] = _now_iso()
    payload["updated_at"] = _now_iso()
    return True


def begin_details_batch_in_payload(
    payload: dict[str, Any],
    *,
    source: str,
    planned_count: int,
    only_failed: bool,
    include_fetched: bool,
    max_attempts: int,
) -> bool:
    if not isinstance(payload, dict):
        return False

    payload["details_batch"] = {
        "status": "running",
        "source": str(source or "").strip(),
        "planned_count": max(0, int(planned_count)),
        "processed_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "remaining_count": max(0, int(planned_count)),
        "current_match_id": "",
        "last_completed_match_id": "",
        "only_failed": bool(only_failed),
        "include_fetched": bool(include_fetched),
        "max_attempts": int(max_attempts),
        "started_at": _now_iso(),
        "finished_at": None,
        "updated_at": _now_iso(),
    }
    payload["updated_at"] = _now_iso()
    return True


def update_details_batch_progress_in_payload(
    payload: dict[str, Any],
    *,
    processed_count: int,
    success_count: int,
    failure_count: int,
    remaining_count: int,
    current_match_id: str = "",
    last_completed_match_id: str = "",
    status: str | None = None,
) -> bool:
    if not isinstance(payload, dict):
        return False

    batch = payload.get("details_batch")
    if not isinstance(batch, dict):
        batch = {}

    if status:
        batch["status"] = str(status)
    batch["processed_count"] = max(0, int(processed_count))
    batch["success_count"] = max(0, int(success_count))
    batch["failure_count"] = max(0, int(failure_count))
    batch["remaining_count"] = max(0, int(remaining_count))
    batch["current_match_id"] = str(current_match_id or "").strip()
    batch["last_completed_match_id"] = str(last_completed_match_id or "").strip()
    batch["updated_at"] = _now_iso()
    payload["details_batch"] = batch
    payload["updated_at"] = _now_iso()
    return True


def finish_details_batch_in_payload(
    payload: dict[str, Any],
    *,
    processed_count: int,
    success_count: int,
    failure_count: int,
    remaining_count: int,
) -> bool:
    batch = payload.get("details_batch") if isinstance(payload, dict) else None
    last_completed_match_id = ""
    if isinstance(batch, dict):
        last_completed_match_id = str(batch.get("last_completed_match_id") or "").strip()

    if not update_details_batch_progress_in_payload(
        payload,
        processed_count=processed_count,
        success_count=success_count,
        failure_count=failure_count,
        remaining_count=remaining_count,
        current_match_id="",
        last_completed_match_id=last_completed_match_id,
        status=("completed_with_failures" if failure_count else "completed"),
    ):
        return False

    batch = payload.get("details_batch")
    if not isinstance(batch, dict):
        return False
    batch["finished_at"] = _now_iso()
    batch["updated_at"] = _now_iso()
    payload["details_batch"] = batch
    payload["updated_at"] = _now_iso()
    return True


def _get_match_row(payload: dict[str, Any], match_id: str) -> dict[str, Any] | None:
    if not match_id or not isinstance(payload, dict):
        return None

    matches = payload.get("matches") or {}
    if not isinstance(matches, dict):
        return None

    match = matches.get(match_id)
    if not isinstance(match, dict):
        return None
    return match


def _safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def _upsert_scores_in_match(
    match: dict[str, Any],
    scores: Any,
    *,
    updated_at: str,
) -> bool:
    if not isinstance(match, dict):
        return False

    incoming = scores if isinstance(scores, dict) else {}
    if not incoming:
        return False

    existing = match.get("scores")
    if not isinstance(existing, dict):
        existing = {}

    changed = False
    for key, value in incoming.items():
        normalized = _normalize_score_value(value)
        if normalized is None or normalized == "":
            continue
        if existing.get(key) != normalized:
            existing[key] = normalized
            changed = True

    if not changed:
        return False

    match["scores"] = sanitize_for_json(existing)
    match["scores_updated_at"] = str(updated_at or _now_iso())
    return True


def _normalize_score_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if re.fullmatch(r"-?\d+", text):
            try:
                return int(text)
            except Exception:
                return text
        return text

    if isinstance(value, numbers.Integral) and not isinstance(value, bool):
        return int(value)

    if isinstance(value, numbers.Real):
        try:
            fv = float(value)
        except Exception:
            return None
        if math.isfinite(fv) and fv.is_integer():
            return int(fv)
        return fv if math.isfinite(fv) else None

    return value


def _has_full_time_scores(item: dict[str, Any]) -> bool:
    scores = item.get("scores")
    if not isinstance(scores, dict):
        return False
    return (
        _normalize_score_value(scores.get("ft_home")) is not None
        and _normalize_score_value(scores.get("ft_away")) is not None
    )


def _normalize_status(item: dict[str, Any]) -> str:
    status = str(item.get("status") or "").strip().lower()
    if status:
        return status

    scores = item.get("scores")
    if isinstance(scores, dict):
        return str(scores.get("state") or "").strip().lower()
    return ""


def _parse_iso_date(date_iso: str) -> Optional[datetime]:
    text = str(date_iso or "").strip()
    if not text:
        return None

    try:
        return datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None


def _derive_score_day_status(
    *,
    match_dt: Optional[datetime],
    now: datetime,
    scored_count: int,
    pending_eligible_count: int,
    future_blocked_count: int,
    completion_grace_days: int,
) -> str:
    if future_blocked_count > 0 and pending_eligible_count == 0 and scored_count == 0:
        return "future"

    if pending_eligible_count <= 0:
        return "complete"

    if match_dt is not None:
        age_days = (now.date() - match_dt.date()).days
        if age_days >= completion_grace_days:
            return "complete"

    if scored_count > 0:
        return "incomplete"
    return "pending"
