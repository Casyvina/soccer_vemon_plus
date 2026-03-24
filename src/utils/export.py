import os
import re
import json
import time
from pathlib import Path
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import Font
from typing import Any

from utils.paths import resolve_matches_dir, resolve_leaguetables_dir
from utils.league_ingest_status import compute_league_ingest_status


def _as_int(value) -> int | None:
    try:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return int(text)
    except Exception:
        return None


def _normalize_round_token(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).lower()


def _round_identity(row) -> str:
    if not isinstance(row, (list, tuple)):
        return ""
    if len(row) <= 1:
        return ""

    round_label = _normalize_round_token(row[1])
    if not round_label:
        return ""

    competition = _normalize_round_token(row[13] if len(row) > 13 else "")
    country = _normalize_round_token(row[14] if len(row) > 14 else "")
    return "|".join(part for part in (country, competition, round_label) if part)


def count_unique_round_groups(rows: list) -> int:
    """
    Count distinct round groups using country+competition+round.
    This avoids collapsing Opening/Closing phases that share the same round numbers.
    """
    keys = set()
    for row in rows or []:
        key = _round_identity(row)
        if key:
            keys.add(key)
    return len(keys)


def _read_previous_total_rounds(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        meta = data.get("meta") if isinstance(data, dict) else {}
        if not isinstance(meta, dict):
            return None
        return _as_int(meta.get("total_rounds"))
    except Exception:
        return None


def _atomic_replace(src: Path, dest: Path, store: Any = None) -> str | None:
    """
    Replace `dest` with `src` atomically when possible.

    If `dest` is locked (e.g., open in Excel), this falls back to saving alongside
    with a timestamped filename so the export still succeeds.
    """
    try:
        os.replace(str(src), str(dest))
        return str(dest)
    except PermissionError as e:
        selection = (getattr(store, "selection", "") or "").upper() if store else ""
        all_odds_single_file = bool(
            getattr(store, "config", None)
            and store.config.get("scraper", "all_odds_single_file", default=True)
        )
        is_all_odds_daily = (
            selection == "ALL_ODDS"
            and all_odds_single_file
            and "@ ALL_ODDS" in dest.name.upper()
        )
        if is_all_odds_daily:
            if store:
                store.logger.log(
                    f"⚠️ ALL_ODDS file is open/locked; close it to update: {dest}",
                    "WARNING",
                )
            try:
                src.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        alt = dest.with_name(f"{dest.stem}__{datetime.now().strftime('%H%M%S')}{dest.suffix}")
        try:
            os.replace(str(src), str(alt))
            if store:
                store.logger.log(
                    f"⚠️ Target file is in use; saved to: {alt}",
                    "WARNING",
                )
            return str(alt)
        except Exception as ex:
            if store:
                store.logger.log(
                    f"❌ Failed to finalize export (kept temp file): {src} ({ex})",
                    "ERROR",
                )
            return str(src)
    except Exception as e:
        if store:
            store.logger.log(f"❌ Failed to finalize export: {dest} ({e})", "ERROR")
        return None


def _save_dataframe_excel_safely(df, dest_path: Path, store: Any):
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = dest_path.with_name(
        f".tmp_{dest_path.stem}_{int(time.time() * 1000)}{dest_path.suffix}"
    )

    try:
        df.to_excel(str(tmp_path), index=False)
    except PermissionError as e:
        store.logger.log(
            f"❌ Permission denied writing Excel: {tmp_path} ({e})",
            "ERROR",
        )
        return None
    except Exception as e:
        store.logger.log(f"❌ Failed to write Excel: {tmp_path} ({e})", "ERROR")
        return None

    return _atomic_replace(tmp_path, dest_path, store=store)


def export_dataframe_to_excel(df, store: Any, prefix="ALL_ODDS", suffix=""):

    if df is None or df.empty:
        store.logger.log("⚠️ No data to export.")
        return None

    base_date_str = store.base_date_str or datetime.now().strftime("%d-%m")
    day_code = (store.day_code or datetime.now().strftime("%a")[:2]).upper()
    if not store.base_date_str or not store.day_code:
        store.logger.log(
            "⚠️ Missing date metadata for filename; using today's date.",
            "WARNING",
        )

    full_date = f"{base_date_str}-{datetime.now().year}-{day_code}"
    selection = store.selection or prefix or "ALL_ODDS"
    time_str = store.timestamp_str or datetime.now().strftime("%I-%M-%S %p")

    all_odds_single_file = bool(
        getattr(store, "config", None)
        and store.config.get("scraper", "all_odds_single_file", default=True)
    )

    if all_odds_single_file and selection.upper() == "ALL_ODDS" and not suffix:
        filename = f"{full_date} @ {selection}.xlsx"
    else:
        filename = f"{full_date} @ {selection}  {time_str} {suffix}.xlsx"
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)

    directory = resolve_matches_dir(store.config)

    dest = directory / filename
    saved_path = _save_dataframe_excel_safely(df, dest, store)
    if not saved_path:
        return None

    store.logger.log(f"✅ Excel file saved: {saved_path}")
    return saved_path


def update_excel_dataframe(df, store: Any, prefix="ALL_ODDS", suffix=""):
    if df is None or df.empty:
        store.logger.log("⚠️ No data to export.")
        return None

    selection = store.selection or prefix or "ALL_ODDS"
    clean_suffix = str(suffix or "").strip()
    if clean_suffix:
        clean_suffix = clean_suffix.lstrip("_- ").strip()
        filename = f"{selection} {clean_suffix}.xlsx"
    else:
        filename = f"{selection}.xlsx"
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    directory = resolve_matches_dir(store.config)

    dest = directory / filename
    saved_path = _save_dataframe_excel_safely(df, dest, store)
    if not saved_path:
        return None

    store.logger.log(f"✅ Excel file saved: {saved_path}")
    return saved_path


def export_scored_all_odds_excel(df, store: Any):
    """
    Save scored ALL_ODDS into a single stable file:
    DD-MM-YYYY-DY @ ALL_ODDS __SCORED.xlsx
    """
    if df is None or df.empty:
        store.logger.log("âš ï¸ No data to export.")
        return None

    selection = str(getattr(store, "selection", "") or "")
    date_match = re.search(r"(\d{2}-\d{2}-\d{4}-[A-Z]{2})", selection)
    if date_match:
        full_date = date_match.group(1)
    else:
        base_date_str = getattr(store, "base_date_str", None) or datetime.now().strftime(
            "%d-%m"
        )
        day_code = (
            getattr(store, "day_code", None) or datetime.now().strftime("%a")[:2]
        ).upper()
        full_date = f"{base_date_str}-{datetime.now().year}-{day_code}"

    filename = f"{full_date} @ ALL_ODDS __SCORED.xlsx"
    filename = re.sub(r'[\\/*?:"<>|]', "_", filename)
    directory = resolve_matches_dir(store.config)
    dest = directory / filename

    saved_path = _save_dataframe_excel_safely(df, dest, store)
    if not saved_path:
        return None

    store.logger.log(f"âœ… Excel file saved: {saved_path}")
    return saved_path


def export_league_data(header: dict, rows: list, store: Any):
    from pathlib import Path
    from datetime import datetime
    import re

    def _slugify(text: str) -> str:
        return re.sub(r"[^\w\-]", "", text.lower().replace(" ", "-"))

    country = _slugify(header.get("country", "unknown"))
    competition = _slugify(header.get("competition", "unknown"))
    season = header.get("season", "unknown").replace("/", "-")
    header["logo_url"] = header.get("logo_url") or header.get("logo", "")

    folder = (
        resolve_leaguetables_dir(store.config) / country / competition / season
    )

    folder.mkdir(parents=True, exist_ok=True)

    match_index_json = folder / "match_index.json"
    computed_total = count_unique_round_groups(rows)
    previous_total = _read_previous_total_rounds(match_index_json)
    if previous_total is not None:
        header["total_rounds"] = max(int(previous_total), int(computed_total))
    else:
        header["total_rounds"] = int(computed_total)

    missing_round_count = 0
    missing_score_count = 0
    for row in rows or []:
        if not isinstance(row, (list, tuple)):
            continue
        round_value = str(row[1] if len(row) > 1 else "").strip()
        if not round_value:
            missing_round_count += 1

        home_goal = str(row[6] if len(row) > 6 else "").strip()
        away_goal = str(row[7] if len(row) > 7 else "").strip()
        if not (home_goal and away_goal):
            missing_score_count += 1

    header["has_incomplete_round_data"] = bool(missing_round_count)
    header["missing_round_match_count"] = int(missing_round_count)
    header["missing_score_match_count"] = int(missing_score_count)

    _save_json(header, rows, match_index_json, store=store)

    store.logger.log(f"🧠 Match index saved: {match_index_json}")

    return str(match_index_json)


def _save_json(header: dict, rows: list, filepath: str, store=None):
    import json

    def _safe_get(row, index: int, default=""):
        try:
            return row[index]
        except Exception:
            return default

    def _text(value) -> str:
        return str(value or "").strip()

    def _is_int_token(value) -> bool:
        return bool(re.fullmatch(r"-?\d+", _text(value)))

    def _resolve_score_state(status_value: str, home_goal, away_goal) -> str:
        status_key = _text(status_value).lower()
        home_text = _text(home_goal)
        away_text = _text(away_goal)
        home_is_num = _is_int_token(home_goal)
        away_is_num = _is_int_token(away_goal)

        if status_key == "fixture":
            if home_is_num and away_is_num:
                return "scored_fixture"
            return "scheduled"

        if home_is_num and away_is_num:
            return "complete"
        if home_text and away_text:
            return "non_numeric"
        if home_text or away_text:
            return "partial"
        return "missing"

    def _parse_row_tail(row) -> tuple[str, str, str]:
        extras = []
        try:
            for value in list(row)[13:]:
                parsed = _text(value)
                if parsed:
                    extras.append(parsed)
        except Exception:
            extras = []

        status = ""
        details = []
        for entry in extras:
            lowered = entry.lower()
            if not status and lowered in {"result", "fixture"}:
                status = lowered
                continue
            details.append(entry)

        competition = details[0] if len(details) > 0 else ""
        country = details[1] if len(details) > 1 else ""
        return status, competition, country

    default_competition = _text(header.get("competition", ""))
    default_country = _text(header.get("country", ""))

    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            existing_data = json.load(f)
        existing_matches = existing_data.get("matches", {})
    else:
        existing_matches = {}

    new_count = 0

    for r in rows:
        if not isinstance(r, (list, tuple)):
            continue

        match_id = str(_safe_get(r, 11, "")).strip()
        if not match_id:
            if store:
                store.logger.log(
                    "⚠️ Skipping malformed row without match_id during JSON save.",
                    "WARNING",
                )
            continue

        existing_entry = existing_matches.get(match_id, {})
        if not isinstance(existing_entry, dict):
            existing_entry = {}
        if match_id not in existing_matches:
            new_count += 1

        tail_status, tail_competition, tail_country = _parse_row_tail(r)
        legacy_status = _text(_safe_get(r, 13, "")).lower()
        if not tail_status and legacy_status in {"result", "fixture"}:
            tail_status = legacy_status

        status_value = tail_status or _text(existing_entry.get("status", "")).lower()
        if status_value not in {"result", "fixture"}:
            status_value = "result" if _text(_safe_get(r, 6, "")) else "fixture"

        competition_value = (
            tail_competition
            or _text(existing_entry.get("competition", ""))
            or default_competition
        )
        country_value = (
            tail_country or _text(existing_entry.get("country", "")) or default_country
        )
        home_goal_value = _safe_get(r, 6, "")
        away_goal_value = _safe_get(r, 7, "")
        score_state_value = _resolve_score_state(
            status_value,
            home_goal_value,
            away_goal_value,
        )

        new_entry = dict(existing_entry)
        new_entry.update(
            {
                "round": _safe_get(r, 1, ""),
                "date": _safe_get(r, 2, ""),
                "time": _safe_get(r, 3, ""),
                "home_team": _safe_get(r, 4, ""),
                "away_team": _safe_get(r, 5, ""),
                "home_goal": home_goal_value,
                "away_goal": away_goal_value,
                "match_url": _safe_get(r, 8, ""),
                "home_icon": _safe_get(r, 9, ""),
                "away_icon": _safe_get(r, 10, ""),
                "odds_fetched": existing_entry.get("odds_fetched", 0),
                "odds": existing_entry.get("odds", {}),
                "odds_status": existing_entry.get("odds_status", ""),
                "odds_updated_at": existing_entry.get("odds_updated_at", ""),
                "status": status_value,
                "score_state": score_state_value,
                "has_complete_score": score_state_value == "complete",
                "competition": competition_value,
                "country": country_value,
            }
        )
        existing_matches[match_id] = new_entry

    now = datetime.now()
    json_data = {
        "meta": header,
        "matches": existing_matches,
        "last_updated": now.isoformat(timespec="seconds"),
    }
    json_data["ingest_status"] = compute_league_ingest_status(
        json_data, reference_time=now
    )

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)

    if store:
        store.logger.log(f"🆕 {new_count} new matches added to match_index.json")


def export_excel_and_csv_from_json(json_path: str, store: Any = None):
    import json
    import pandas as pd
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from pathlib import Path

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    folder = Path(json_path).parent
    meta = data.get("meta", {})
    matches = data.get("matches", {})

    season = meta.get("season", "unknown").replace("/", "-")
    competition = re.sub(r"[^\w\-]", "", meta.get("competition", "unknown").lower())
    excel_path = folder / f"{season}-{competition}.xlsx"
    csv_path = folder / f"{season}-{competition}.csv"

    # Build rows
    rows = []

    def _extract_round_number(round_str):
        match = re.search(r"(\d+)", str(round_str))
        return int(match.group(1)) if match else -1

    sorted_matches = sorted(
        matches.items(),
        key=lambda item: _extract_round_number(item[1].get("round", "")),
        reverse=True,
    )

    for match_id, m in sorted_matches:
        odds = m.get("odds", {})
        row = {
            "MatchId": match_id,
            "Competition": m.get("competition") or meta.get("competition"),
            "Country": m.get("country") or meta.get("country"),
            "Round": m.get("round"),
            "Date": m.get("date"),
            "Time": m.get("time"),
            "HomeTeam": m.get("home_team"),
            "AwayTeam": m.get("away_team"),
            "HomeGoal": m.get("home_goal"),
            "AwayGoal": m.get("away_goal"),
            "MatchUrl": m.get("match_url"),
            "OddsFetched": m.get("odds_fetched", 0),
            "Status": m.get("status"),
            "ScoreState": m.get("score_state"),
            "Odds1": odds.get("1X2", {}).get("odds", {}).get("1"),
            "OddsX": odds.get("1X2", {}).get("odds", {}).get("X"),
            "Odds2": odds.get("1X2", {}).get("odds", {}).get("2"),
            "GG": odds.get("btts", {}).get("odds", {}).get("yes"),
            "NG": odds.get("btts", {}).get("odds", {}).get("no"),
            "Over1.5": odds.get("over_under", {})
            .get("full_time_1_5", [{}])[0]
            .get("over"),
            "Under1.5": odds.get("over_under", {})
            .get("full_time_1_5", [{}])[0]
            .get("under"),
        }
        rows.append(row)

    df = pd.DataFrame(rows)

    # Use temp + replace to reduce issues with open/locked files.
    tmp_excel = excel_path.with_name(
        f".tmp_{excel_path.stem}_{int(time.time() * 1000)}{excel_path.suffix}"
    )
    tmp_csv = csv_path.with_name(
        f".tmp_{csv_path.stem}_{int(time.time() * 1000)}{csv_path.suffix}"
    )

    try:
        df.to_excel(tmp_excel, index=False)
        df.to_csv(tmp_csv, index=False)

        final_excel = _atomic_replace(tmp_excel, excel_path, store=store)
        final_csv = _atomic_replace(tmp_csv, csv_path, store=store)
        return final_excel, final_csv
    except Exception as e:
        if store:
            store.logger.log(f"❌ Failed to export league data: {e}", "ERROR")
        return None, None
