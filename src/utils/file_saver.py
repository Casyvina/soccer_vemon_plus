import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from utils.all_odds_store import extract_match_id, sanitize_for_json
from utils.paths import resolve_raw_dir


def save_raw_match_json(
    match_data: dict,
    timestamp: bool = True,
    base_dir: Optional[str] = None,
    config=None,
    date_iso: Optional[str] = None,
    match_id: Optional[str] = None,
) -> str:
    """Save raw match data JSON into a date-stamped folder.

    - If `base_dir` is provided, saves to: <base_dir>/YYYY-MM-DD/
    - Else uses the app's resolved raw folder (defaults to Soccer_Place_Data).
    """

    resolved_base = (base_dir or "").strip()

    if resolved_base:
        root = Path(resolved_base).expanduser()
    else:
        root = resolve_raw_dir(config)

    date_str = (date_iso or "").strip() or datetime.now().strftime("%Y-%m-%d")
    raw_dir = root / date_str
    raw_dir.mkdir(parents=True, exist_ok=True)

    home = match_data.get("match_details", {}).get("home_team", "unknown")
    away = match_data.get("match_details", {}).get("away_team", "unknown")

    resolved_match_id = (match_id or "").strip()
    if not resolved_match_id:
        resolved_match_id = extract_match_id(str(match_data.get("url") or ""))

    time_tag = datetime.now().strftime("%H%M%S") if timestamp else ""

    if resolved_match_id and not timestamp:
        filename = f"{resolved_match_id}.json"
    elif resolved_match_id and timestamp:
        filename = f"{resolved_match_id}__{time_tag}.json"
    else:
        filename = f"{home}_vs_{away}_{time_tag}.json".replace(" ", "_").strip("_")

    filepath = raw_dir / filename

    # Write atomically to avoid partial/corrupt json when the app stops mid-write
    tmp = filepath.with_name(f".tmp_{filepath.stem}_{int(datetime.now().timestamp()*1000)}{filepath.suffix}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sanitize_for_json(match_data), f, ensure_ascii=False, indent=2, allow_nan=False)
    os.replace(str(tmp), str(filepath))

    return str(filepath)
