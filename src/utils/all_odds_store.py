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
    merged["updated_at"] = datetime.now().isoformat(timespec="seconds")

    matches: dict[str, Any] = merged["matches"] or {}
    for match_id, item in (snapshot.get("matches") or {}).items():
        if not match_id:
            continue

        prev = matches.get(match_id)
        if not prev:
            stats.added += 1
            item = dict(item)
            item.setdefault("first_seen_at", merged["updated_at"])
            item["last_seen_at"] = merged["updated_at"]
            item.setdefault("details_fetched", False)
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

        prev["last_seen_at"] = merged["updated_at"]
        prev.setdefault("details_fetched", False)
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

    match["details_fetched"] = bool(fetched)
    if fetched:
        match["details_fetched_at"] = datetime.now().isoformat(timespec="seconds")

    payload["matches"] = matches
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(path, payload)
    return True


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

    changed = False
    for key, value in (scores or {}).items():
        if value is None or value == "":
            continue
        if existing.get(key) != value:
            existing[key] = value
            changed = True

    if not changed:
        return False

    match["scores"] = sanitize_for_json(existing)
    match["scores_updated_at"] = datetime.now().isoformat(timespec="seconds")
    payload["matches"] = matches
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return True


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
    prev = match.get("details_fetched")
    if prev is fetched:
        return False

    match["details_fetched"] = fetched
    if fetched:
        match["details_fetched_at"] = datetime.now().isoformat(timespec="seconds")
    payload["matches"] = matches
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return True
