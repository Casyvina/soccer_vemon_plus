from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

MATCH_FINALIZATION_HOURS = 3
STALE_REFRESH_HOURS = 24


def _to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _extract_round_number(value: Any) -> int | None:
    match = re.search(r"(\d+)", str(value or ""))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _parse_season_years(season: str) -> tuple[int | None, int | None]:
    years = re.findall(r"\d{2,4}", str(season or ""))
    if not years:
        return None, None

    parsed: list[int] = []
    for token in years[:2]:
        year = int(token)
        if len(token) == 2:
            year += 2000
        parsed.append(year)

    if len(parsed) == 1:
        return parsed[0], parsed[0]
    return parsed[0], parsed[1]


def _parse_match_datetime(
    date_text: str | None,
    time_text: str | None,
    season_start_year: int | None,
    season_end_year: int | None,
) -> datetime | None:
    date_match = re.search(r"(\d{1,2})\D+(\d{1,2})(?:\D+(\d{2,4}))?", str(date_text or ""))
    time_match = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", str(time_text or ""))
    if not date_match or not time_match:
        return None

    day = int(date_match.group(1))
    month = int(date_match.group(2))

    year_text = date_match.group(3)
    if year_text:
        year = int(year_text)
        if len(year_text) == 2:
            year += 2000
    elif season_start_year and season_end_year:
        year = season_start_year if month >= 7 else season_end_year
    elif season_start_year:
        year = season_start_year
    elif season_end_year:
        year = season_end_year
    else:
        year = datetime.now().year

    hour = int(time_match.group(1))
    minute = int(time_match.group(2))
    second = int(time_match.group(3) or 0)

    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None


def compute_league_ingest_status(
    payload: dict[str, Any],
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    now = reference_time or datetime.now()

    meta = payload.get("meta", {}) if isinstance(payload, dict) else {}
    matches = payload.get("matches", {}) if isinstance(payload, dict) else {}
    if not isinstance(matches, dict):
        matches = {}

    season_start_year, season_end_year = _parse_season_years(str(meta.get("season", "")))

    result_rounds: set[int] = set()
    unresolved_fixture_kickoffs_by_round: dict[int, list[datetime]] = {}
    unresolved_fixture_rounds_without_kickoff: set[int] = set()
    fallback_fixture_kickoffs_by_round: dict[int, list[datetime]] = {}

    for match in matches.values():
        if not isinstance(match, dict):
            continue

        round_no = _extract_round_number(match.get("round"))
        if round_no is None:
            continue

        status = str(match.get("status") or "").strip().lower()
        if status == "result":
            result_rounds.add(round_no)

        home_goal_text = str(match.get("home_goal") or "").strip()
        away_goal_text = str(match.get("away_goal") or "").strip()
        has_complete_score = bool(
            re.fullmatch(r"-?\d+", home_goal_text)
            and re.fullmatch(r"-?\d+", away_goal_text)
        )
        if status == "fixture":
            kickoff = _parse_match_datetime(
                date_text=str(match.get("date") or ""),
                time_text=str(match.get("time") or ""),
                season_start_year=season_start_year,
                season_end_year=season_end_year,
            )
            if has_complete_score:
                # Fixtures with resolved numeric FT score are historical.
                result_rounds.add(round_no)
                continue
            if kickoff is None:
                unresolved_fixture_rounds_without_kickoff.add(round_no)
                continue
            fallback_fixture_kickoffs_by_round.setdefault(round_no, []).append(kickoff)
            # Only upcoming fixtures should drive next due windows.
            if kickoff >= now:
                unresolved_fixture_kickoffs_by_round.setdefault(round_no, []).append(
                    kickoff
                )

    current_round = max(result_rounds) if result_rounds else None

    fixture_rounds = sorted(unresolved_fixture_kickoffs_by_round.keys())
    next_round: int | None = None
    next_round_first_kickoff: datetime | None = None
    if fixture_rounds:
        # Use earliest upcoming unresolved fixture by kickoff time (not round number).
        earliest = min(
            (
                (kickoff, round_no)
                for round_no, kickoff_list in unresolved_fixture_kickoffs_by_round.items()
                for kickoff in kickoff_list
            ),
            key=lambda item: item[0],
        )
        next_round_first_kickoff, next_round = earliest[0], earliest[1]
    elif unresolved_fixture_rounds_without_kickoff:
        # Upcoming fixture rows exist but kickoff could not be parsed.
        next_round = min(unresolved_fixture_rounds_without_kickoff)
    elif fallback_fixture_kickoffs_by_round:
        # No future fixtures detected; pick nearest recent unresolved kickoff and move on.
        nearest_past = max(
            (
                (kickoff, round_no)
                for round_no, kickoff_list in fallback_fixture_kickoffs_by_round.items()
                for kickoff in kickoff_list
            ),
            key=lambda item: item[0],
        )
        next_round_first_kickoff, next_round = nearest_past[0], nearest_past[1]

    next_round_last_kickoff: datetime | None = None
    if next_round is not None:
        kickoff_list = unresolved_fixture_kickoffs_by_round.get(next_round, [])
        if not kickoff_list:
            kickoff_list = fallback_fixture_kickoffs_by_round.get(next_round, [])
        if kickoff_list:
            # Wait until this earliest-upcoming round is fully expected to finish.
            next_round_last_kickoff = max(kickoff_list)
        elif next_round_first_kickoff is not None:
            next_round_last_kickoff = next_round_first_kickoff

    update_due_at = (
        next_round_last_kickoff + timedelta(hours=MATCH_FINALIZATION_HOURS)
        if next_round_last_kickoff is not None
        else now + timedelta(hours=STALE_REFRESH_HOURS)
    )

    return {
        "current_round_at_ingest": current_round,
        "next_round": next_round,
        "next_round_last_kickoff_at": _to_iso(next_round_last_kickoff),
        "update_due_at": _to_iso(update_due_at),
        "computed_at": _to_iso(now),
        "last_ingested_at": _to_iso(now),
        "match_count": len(matches),
    }
