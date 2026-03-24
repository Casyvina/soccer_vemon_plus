from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from headless.parsers.common import parse_form_icons, parse_goals, safe_int, text_or_empty


def _determine_seasonal_stage(
    mp: int, total_teams: int, form_icons: list[str] | None = None
) -> str:
    try:
        if total_teams <= 1:
            return "unknown"

        season_active = False
        if form_icons:
            season_active = any("?" in item for item in form_icons)

        if season_active:
            leg_fraction = mp / (total_teams - 1)
            if leg_fraction <= 1:
                return "firstleg"
            if leg_fraction <= 2:
                return "secondleg"
            if leg_fraction <= 3:
                return "thirdleg"
            return "fourthleg"

        leg_count = mp // (total_teams - 1)
        if leg_count <= 1:
            return "firstleg"
        if leg_count == 2:
            return "secondleg"
        if leg_count == 3:
            return "thirdleg"
        return "fourthleg"
    except Exception:
        return "unknown"


def _parse_row(row_element: Tag, total_teams: int) -> dict | None:
    if row_element is None:
        return None

    try:
        rank_div = row_element.select_one(".table__cell--rank .tableCellRank")
        rank_text = text_or_empty(rank_div).rstrip(".")
        rank = safe_int(rank_text)
        promotion_title = str(rank_div.get("title") or "").strip() if rank_div else ""

        team_name = text_or_empty(row_element.select_one(".tableCellParticipant__name"))
        value_cells = [
            text_or_empty(node) for node in row_element.select("span.table__cell--value")
        ]

        mp = safe_int(value_cells[0]) if len(value_cells) > 0 else 0
        w = safe_int(value_cells[1]) if len(value_cells) > 1 else 0
        d = safe_int(value_cells[2]) if len(value_cells) > 2 else 0
        l = safe_int(value_cells[3]) if len(value_cells) > 3 else 0
        goals_text = value_cells[4] if len(value_cells) > 4 else "0:0"
        gf, ga = parse_goals(goals_text)
        gd = safe_int(value_cells[5]) if len(value_cells) > 5 else gf - ga
        pts = safe_int(value_cells[6]) if len(value_cells) > 6 else (3 * w + d)

        form_cell = row_element.select_one(".table__cell--form")
        form = parse_form_icons(form_cell)
        actual_points = f"{(w + d)}/{mp}" if mp else "0/0"
        seasonal_stage = _determine_seasonal_stage(mp, total_teams, form)

        return {
            "rank": rank,
            "team": team_name,
            "promotion_title": promotion_title,
            "mp": mp,
            "w": w,
            "d": d,
            "l": l,
            "goals_for": gf,
            "goals_against": ga,
            "gd": gd,
            "pts": pts,
            "form": form,
            "actual_points": actual_points,
            "seasonal_stage": seasonal_stage,
        }
    except Exception:
        return None


def parse_standings_page(
    html: str,
    home_team_name: str,
    away_team_name: str,
) -> dict:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    rows = soup.select(".ui-table__body .ui-table__row")
    total_teams = len(rows)

    result_all = []
    promotion = []
    relegation = []
    home_team = None
    away_team = None

    home_name_lc = str(home_team_name or "").strip().lower()
    away_name_lc = str(away_team_name or "").strip().lower()

    for row in rows:
        parsed = _parse_row(row, total_teams)
        if not parsed:
            continue

        result_all.append(parsed)
        title = str(parsed.get("promotion_title") or "").lower()
        if "promotion" in title or "champ" in title or "promoted" in title:
            promotion.append(parsed)
        elif "relegation" in title or "relegat" in title:
            relegation.append(parsed)

        team_name = str(parsed.get("team") or "").lower()
        if home_name_lc and home_name_lc in team_name:
            home_team = parsed
        if away_name_lc and away_name_lc in team_name:
            away_team = parsed

    return {
        "total_rows": total_teams,
        "promotions": len(promotion),
        "relegations": len(relegation),
        "home_team": home_team,
        "away_team": away_team,
        "all": result_all,
    }
