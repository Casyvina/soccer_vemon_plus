import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from utils.all_odds_store import extract_match_id


DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _to_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _format_market_day_label(date_iso: str) -> str:
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        return dt.strftime("%a %d %b %Y")
    except Exception:
        return date_iso


def _iso_to_ddmmyyyy(date_iso: str) -> str:
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        return dt.strftime("%d.%m.%Y")
    except Exception:
        return date_iso


def _extract_team_name(team_data: Any) -> str:
    if isinstance(team_data, dict):
        return _as_str(team_data.get("team"))
    return _as_str(team_data)


def _match_winner_label(
    home: str, away: str, score_home: Any, score_away: Any
) -> str:
    h = _to_int(score_home)
    a = _to_int(score_away)
    if h is None or a is None:
        return ""
    if h > a:
        return home
    if a > h:
        return away
    return "Draw"


def _normalize_h2h_status(status: str, home_team: str, away_team: str) -> str:
    s = _as_str(status)
    if not s:
        return ""
    if s == home_team:
        return "H"
    if s == away_team:
        return "A"
    if s.lower() == "draw":
        return "D"
    return ""


def _result_icon(team_goals: Any, opp_goals: Any) -> str:
    tg = _to_int(team_goals)
    og = _to_int(opp_goals)
    if tg is None or og is None:
        return "-"
    if tg > og:
        return "W"
    if tg < og:
        return "L"
    return "D"


def _compute_wdl_status(team_goals: Any, opp_goals: Any) -> str:
    icon = _result_icon(team_goals, opp_goals)
    return icon if icon in {"W", "D", "L"} else ""


def _extract_head_to_head_matches(match: dict) -> list[dict]:
    for section in _safe_list(match.get("h2h")):
        title = _as_str(_safe_dict(section).get("section_title")).upper()
        if "HEAD-TO-HEAD" in title:
            return [m for m in _safe_list(_safe_dict(section).get("matches")) if isinstance(m, dict)][
                :5
            ]
    return []


def _extract_last_matches_for_team(match: dict, team_name: str) -> list[dict]:
    target = f"LAST MATCHES: {team_name.upper()}"
    for section in _safe_list(match.get("h2h")):
        title = _as_str(_safe_dict(section).get("section_title")).upper()
        if title == target:
            return [m for m in _safe_list(_safe_dict(section).get("matches")) if isinstance(m, dict)][
                :5
            ]
    return []


def _extract_goal_triplet(table_block: dict, team_side: str) -> str:
    team = _safe_dict(table_block.get(f"{team_side}_team"))
    gf = _to_int(team.get("goals_for"))
    ga = _to_int(team.get("goals_against"))
    if gf is None or ga is None:
        return ""
    return f"{gf}:{ga} {gf - ga}"


def _goal_profile(match: dict, team_side: str) -> str:
    overall = _safe_dict(match.get("table"))
    home_only = _safe_dict(match.get("table_home_only"))
    away_only = _safe_dict(match.get("table_away_only"))
    parts = [
        _extract_goal_triplet(overall, team_side),
        _extract_goal_triplet(home_only, team_side),
        _extract_goal_triplet(away_only, team_side),
    ]
    return " || ".join(parts)


def _parse_date_any(date_text: str) -> datetime | None:
    date_text = _as_str(date_text)
    if not date_text:
        return None

    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(date_text, fmt)
        except Exception:
            continue
    return None


def _days_ago(current_date: str, past_date: str) -> str:
    current_dt = _parse_date_any(current_date)
    past_dt = _parse_date_any(past_date)
    if not current_dt or not past_dt:
        return ""
    delta = (current_dt - past_dt).days
    if delta < 0:
        return ""
    return f"{delta} days ago"


def _analyzer_code(match: dict) -> str:
    stage = (
        _as_str(_safe_dict(_safe_dict(match.get("table")).get("home_team")).get("seasonal_stage")).lower()
    )
    mapping = {
        "firstleg": "FL",
        "secondleg": "SL",
        "thirdleg": "TL",
        "fourthleg": "FL",
        "cup": "CPF",
        "friendly": "CPF",
    }
    return mapping.get(stage, "")


def _build_weekly_rows(match: dict, team_name: str) -> list[dict]:
    rows: list[dict] = []
    for item in _extract_last_matches_for_team(match, team_name):
        home = _as_str(item.get("home"))
        away = _as_str(item.get("away"))
        is_home = home == team_name
        team_goals = _as_str(item.get("score_home" if is_home else "score_away"))
        opp_goals = _as_str(item.get("score_away" if is_home else "score_home"))
        status = _as_str(item.get("result")).upper()
        if status not in {"W", "D", "L"}:
            status = _compute_wdl_status(team_goals, opp_goals)
        rows.append(
            {
                "event": _as_str(item.get("event")),
                "area": "H" if is_home else "A",
                "opponent": away if is_home else home,
                "status": status,
                "date": _as_str(item.get("date")),
                "teamGoals": team_goals,
                "oppGoals": opp_goals,
            }
        )
    return rows


def _build_last_opponent_rows(match: dict, team_name: str) -> list[dict]:
    last_data = _safe_dict(_safe_dict(match.get("last_matches")).get(team_name))
    h2h_sections = _safe_list(last_data.get("h2h"))

    opponent = ""
    for section in h2h_sections:
        title = _as_str(_safe_dict(section).get("section_title")).upper()
        if "HEAD-TO-HEAD" not in title:
            continue
        matches = _safe_list(_safe_dict(section).get("matches"))
        if not matches:
            continue
        first = _safe_dict(matches[0])
        home = _as_str(first.get("home"))
        away = _as_str(first.get("away"))
        opponent = away if home == team_name else home
        break

    if not opponent:
        return []

    rows: list[dict] = []
    for section in h2h_sections:
        title = _as_str(_safe_dict(section).get("section_title")).upper()
        if "HEAD-TO-HEAD" not in title:
            continue
        for item in _safe_list(_safe_dict(section).get("matches")):
            entry = _safe_dict(item)
            home = _as_str(entry.get("home"))
            away = _as_str(entry.get("away"))
            if {home, away} != {team_name, opponent}:
                continue
            is_home = home == team_name
            team_goals = _as_str(entry.get("score_home" if is_home else "score_away"))
            opp_goals = _as_str(entry.get("score_away" if is_home else "score_home"))
            rows.append(
                {
                    "event": _as_str(entry.get("event")),
                    "area": "H" if is_home else "A",
                    "status": _compute_wdl_status(team_goals, opp_goals),
                    "date": _as_str(entry.get("date")),
                    "teamGoals": team_goals,
                    "oppGoals": opp_goals,
                }
            )
    return rows


def _build_boost(match: dict, team_name: str, weekly_rows: list[dict]) -> str:
    form_icons: list[str] = []
    for row in weekly_rows[:5]:
        status = _as_str(row.get("status")).upper()
        if status not in {"W", "D", "L"}:
            status = _result_icon(row.get("teamGoals"), row.get("oppGoals"))
        if status not in {"W", "D", "L"}:
            status = "-"
        form_icons.append(status)

    form_icons = list(reversed(form_icons))
    if len(form_icons) < 5:
        form_icons = (["-"] * (5 - len(form_icons))) + form_icons

    event_code = _as_str(weekly_rows[0].get("event")) if weekly_rows else ""
    return f"{''.join(form_icons)}||{event_code}"


def _build_h2h_rows(
    match: dict, home_team: str, away_team: str, current_date: str, h2h_matches: list[dict]
) -> list[dict]:
    rows: list[dict] = [
        {
            "event": "?",
            "area": "H",
            "status": "?",
            "date": current_date,
            "homeGoals": "?",
            "awayGoals": "?",
        }
    ]

    for item in h2h_matches:
        home = _as_str(item.get("home"))
        away = _as_str(item.get("away"))
        score_home = _as_str(item.get("score_home"))
        score_away = _as_str(item.get("score_away"))

        if home_team == home:
            area = "H"
            home_goals = score_home
            away_goals = score_away
        elif home_team == away:
            area = "A"
            home_goals = score_away
            away_goals = score_home
        else:
            area = ""
            home_goals = ""
            away_goals = ""

        rows.append(
            {
                "event": _as_str(item.get("event")),
                "area": area,
                "status": _match_winner_label(home, away, score_home, score_away),
                "date": _as_str(item.get("date")),
                "homeGoals": home_goals,
                "awayGoals": away_goals,
                "url": _as_str(item.get("url")),
            }
        )
    return rows


def _promotion_relegation_text(promotions: Any, relegations: Any) -> str:
    p = _as_str(promotions) or "0"
    r = _as_str(relegations) or "0"
    return f"{p} & {r}"


def _build_current_standing_row(match: dict) -> dict:
    table = _safe_dict(match.get("table"))
    home = _safe_dict(table.get("home_team"))
    away = _safe_dict(table.get("away_team"))
    return {
        "hPosition": _as_str(home.get("rank")),
        "aPosition": _as_str(away.get("rank")),
        "hActualPoint": _as_str(home.get("actual_points")),
        "aActualPoint": _as_str(away.get("actual_points")),
        "hTablePoint": _as_str(home.get("pts")),
        "aTablePoint": _as_str(away.get("pts")),
        "totalTeams": _as_str(table.get("total_rows")),
        "htf": "".join(_safe_list(home.get("form"))),
        "atf": "".join(_safe_list(away.get("form"))),
        "hTitle": _as_str(home.get("promotion_title")),
        "aTitle": _as_str(away.get("promotion_title")),
        "promotionRelegation": _promotion_relegation_text(
            table.get("promotions"), table.get("relegations")
        ),
        "areaReference": "H",
        "seasonKey": _as_str(home.get("seasonal_stage")),
    }


def _build_h2h_standing_rows(
    match: dict, home_team: str, h2h_matches: list[dict]
) -> list[dict]:
    rows: list[dict] = []
    standings_map = _safe_dict(match.get("h2h_standings"))

    for item in h2h_matches:
        match_url = _as_str(item.get("url"))
        match_id = extract_match_id(match_url)
        data = _safe_dict(standings_map.get(match_id))
        if not data or int(data.get("total_rows") or 0) == 0:
            continue
        home_data = data.get("home_team")
        away_data = data.get("away_team")
        home_data = _safe_dict(home_data) if isinstance(home_data, dict) else {}
        away_data = _safe_dict(away_data) if isinstance(away_data, dict) else {}

        home_name = _extract_team_name(home_data)
        away_name = _extract_team_name(away_data)

        if home_team and home_team == home_name:
            h_team = home_data
            a_team = away_data
            area_ref = "H"
        elif home_team and home_team == away_name:
            h_team = away_data
            a_team = home_data
            area_ref = "A"
        else:
            h_team = {}
            a_team = {}
            raw_home = _as_str(item.get("home"))
            raw_away = _as_str(item.get("away"))
            area_ref = "H" if home_team and home_team == raw_home else ("A" if home_team and home_team == raw_away else "")

        rows.append(
            {
                "hPosition": _as_str(h_team.get("rank")),
                "aPosition": _as_str(a_team.get("rank")),
                "hActualPoint": _as_str(h_team.get("actual_points")),
                "aActualPoint": _as_str(a_team.get("actual_points")),
                "hTablePoint": _as_str(h_team.get("pts")),
                "aTablePoint": _as_str(a_team.get("pts")),
                "totalTeams": _as_str(data.get("total_rows")),
                "htf": "".join(_safe_list(h_team.get("form"))),
                "atf": "".join(_safe_list(a_team.get("form"))),
                "hTitle": _as_str(h_team.get("promotion_title")),
                "aTitle": _as_str(a_team.get("promotion_title")),
                "promotionRelegation": _promotion_relegation_text(
                    data.get("promotions"), data.get("relegations")
                ),
                "areaReference": area_ref,
                "seasonKey": _as_str(h_team.get("seasonal_stage")),
            }
        )
    return rows


def _read_json(path: Path) -> dict:
    encodings = ("utf-8", "utf-8-sig")
    for enc in encodings:
        try:
            with open(path, "r", encoding=enc) as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            continue
    raise ValueError(f"Unable to parse JSON: {path}")


def _iter_date_folders(raw_root: Path) -> Iterable[Path]:
    if not raw_root.exists() or not raw_root.is_dir():
        return []
    dirs = [p for p in raw_root.iterdir() if p.is_dir() and DATE_DIR_PATTERN.match(p.name)]
    return sorted(dirs, key=lambda p: p.name)


def iter_market_date_dirs(raw_root: Path) -> list[Path]:
    return list(_iter_date_folders(raw_root))


def build_market_match(
    match: dict, fallback_date_iso: str, fallback_match_id: str = ""
) -> dict:
    breadcrumb = _safe_dict(match.get("breadcrumb"))
    details = _safe_dict(match.get("match_details"))
    table = _safe_dict(match.get("table"))
    home_team = _as_str(details.get("home_team"))
    away_team = _as_str(details.get("away_team"))
    current_date = _as_str(details.get("date")) or _iso_to_ddmmyyyy(fallback_date_iso)

    url = _as_str(match.get("url"))
    match_id = extract_match_id(url) or fallback_match_id

    h2h_matches = _extract_head_to_head_matches(match)
    h2h_rows = _build_h2h_rows(match, home_team, away_team, current_date, h2h_matches)
    standings_rows = [_build_current_standing_row(match)] + _build_h2h_standing_rows(
        match, home_team, h2h_matches
    )

    home_weekly = _build_weekly_rows(match, home_team)
    away_weekly = _build_weekly_rows(match, away_team)

    home_last_status = home_weekly[0]["status"] if home_weekly else ""
    away_last_status = away_weekly[0]["status"] if away_weekly else ""
    home_last_area = home_weekly[0]["area"] if home_weekly else ""
    away_last_area = away_weekly[0]["area"] if away_weekly else ""
    h2h_last_status = (
        _normalize_h2h_status(h2h_rows[1]["status"], home_team, away_team)
        if len(h2h_rows) > 1
        else ""
    )

    match_tag = _as_str(_safe_dict(table.get("home_team")).get("seasonal_stage")) or "CUP/FRIENDLY"

    home_last_date = home_weekly[0]["date"] if home_weekly else ""
    away_last_date = away_weekly[0]["date"] if away_weekly else ""

    payload = {
        "matchId": match_id,
        "url": url,
        "kickoffTime": _as_str(details.get("time")),
        "homeTeam": home_team,
        "awayTeam": away_team,
        "location": {
            "homeGoalProfile": _goal_profile(match, "home"),
            "awayGoalProfile": _goal_profile(match, "away"),
            "country": _as_str(breadcrumb.get("country")),
            "competition": _as_str(breadcrumb.get("competition")).upper(),
            "round": _as_str(breadcrumb.get("stage")).upper(),
        },
        "contextSummary": {
            "homeBoost": _build_boost(match, home_team, home_weekly),
            "awayBoost": _build_boost(match, away_team, away_weekly),
            "homeLastGame": _days_ago(current_date, home_last_date),
            "awayLastGame": _days_ago(current_date, away_last_date),
            "cfdLevel": "",
            "analyzer": _analyzer_code(match).upper(),
        },
        "h2h": {
            "currentDate": current_date,
            "rows": h2h_rows,
        },
        "standings": standings_rows,
        "homeWeekly": home_weekly,
        "awayWeekly": away_weekly,
        "tags": {
            "country": _as_str(breadcrumb.get("country")),
            "competition": _as_str(breadcrumb.get("competition")).upper(),
            "matchTag": match_tag,
        },
        "filterFields": {
            "homeCP": standings_rows[0]["hPosition"] if standings_rows else "",
            "awayCP": standings_rows[0]["aPosition"] if standings_rows else "",
            "homeLastStatus": home_last_status,
            "awayLastStatus": away_last_status,
            "homeLastArea": home_last_area,
            "awayLastArea": away_last_area,
            "h2hLastStatus": h2h_last_status,
            "country": _as_str(breadcrumb.get("country")),
            "competition": _as_str(breadcrumb.get("competition")).upper(),
            "matchTags": match_tag,
        },
    }
    return payload


def build_market_day_from_folder(date_dir: Path, logger=None) -> tuple[dict, dict]:
    date_iso = date_dir.name
    stats = {
        "date_folder": date_iso,
        "json_files": 0,
        "market_matches": 0,
        "parse_errors": 0,
    }

    matches: list[dict] = []
    for json_file in sorted(date_dir.glob("*.json"), key=lambda p: p.name.lower()):
        stats["json_files"] += 1
        try:
            payload = _read_json(json_file)
            market_match = build_market_match(
                payload, fallback_date_iso=date_iso, fallback_match_id=json_file.stem
            )
            matches.append(market_match)
            stats["market_matches"] += 1
        except Exception as e:
            stats["parse_errors"] += 1
            if logger:
                logger.log(f"WARN Failed parsing {json_file}: {e}", "WARNING")

    matches.sort(
        key=lambda m: (
            m.get("kickoffTime", ""),
            m.get("homeTeam", ""),
            m.get("awayTeam", ""),
        )
    )

    day = {
        "id": date_iso,
        "label": _format_market_day_label(date_iso),
        "matches": matches,
    }
    return day, stats


def build_market_days_from_raw(raw_root: Path, logger=None) -> tuple[list[dict], dict]:
    stats = {
        "date_folders": 0,
        "json_files": 0,
        "market_matches": 0,
        "parse_errors": 0,
    }
    by_date: dict[str, dict] = {}

    for date_dir in iter_market_date_dirs(raw_root):
        stats["date_folders"] += 1
        day, day_stats = build_market_day_from_folder(date_dir, logger=logger)
        stats["json_files"] += int(day_stats.get("json_files", 0))
        stats["market_matches"] += int(day_stats.get("market_matches", 0))
        stats["parse_errors"] += int(day_stats.get("parse_errors", 0))
        by_date[day["id"]] = day

    market_days: list[dict] = []
    for date_iso in sorted(by_date.keys()):
        market_days.append(by_date[date_iso])

    return market_days, stats


def save_market_payload(market_days: list[dict], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"marketDays": market_days}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return output_path
