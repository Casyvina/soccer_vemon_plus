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


def _build_col_map(header_block: Tag) -> dict[str, int]:
    """
    Return {HEADER_TEXT_UPPER: value_cell_index}.
    Skips the first two cells (# rank and team/division name).
    """
    cells = [
        c.get_text(strip=True).upper()
        for c in header_block.select(".ui-table__headerCell")
    ]
    # cells[0] = "#", cells[1] = team/division name — not value cells
    return {name: i for i, name in enumerate(cells[2:])}


def _cell(value_cells: list[str], col_map: dict[str, int], *names: str, fallback_idx: int = -1) -> str:
    for name in names:
        if name in col_map:
            idx = col_map[name]
            return value_cells[idx] if idx < len(value_cells) else ""
    if fallback_idx >= 0:
        return value_cells[fallback_idx] if fallback_idx < len(value_cells) else ""
    return ""


def _parse_row(
    row_element: Tag,
    total_teams: int,
    col_map: dict[str, int] | None = None,
) -> dict | None:
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

        cm = col_map or {}
        is_mls = "WP" in cm or "LP" in cm

        mp = safe_int(_cell(value_cells, cm, "MP", fallback_idx=0))
        w = safe_int(_cell(value_cells, cm, "W", fallback_idx=1))

        if is_mls:
            # MLS format: MP, W, WP, LP, L, G, Pts — no draws, no explicit GD column
            d = 0
            l = safe_int(_cell(value_cells, cm, "L", fallback_idx=4))
            goals_text = _cell(value_cells, cm, "G", fallback_idx=5)
            gf, ga = parse_goals(goals_text)
            gd = gf - ga
            pts = safe_int(_cell(value_cells, cm, "PTS", fallback_idx=6))
        else:
            # Standard format: MP, W, D, L, G, GD, Pts
            d = safe_int(_cell(value_cells, cm, "D", fallback_idx=2))
            l = safe_int(_cell(value_cells, cm, "L", fallback_idx=3))
            goals_text = _cell(value_cells, cm, "G", fallback_idx=4)
            gf, ga = parse_goals(goals_text)
            gd_str = _cell(value_cells, cm, "GD", fallback_idx=5)
            gd = safe_int(gd_str) if gd_str and ":" not in gd_str else gf - ga
            pts = safe_int(_cell(value_cells, cm, "PTS", fallback_idx=6))

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

    # Collect all ui-table blocks — each may have its own column format
    table_blocks = soup.select(".ui-table")

    # Fallback: if no ui-table wrappers, use old flat selector
    if not table_blocks:
        rows = soup.select(".ui-table__body .ui-table__row")
        return _parse_rows(rows, len(rows), col_map=None,
                           home_name_lc=str(home_team_name or "").strip().lower(),
                           away_name_lc=str(away_team_name or "").strip().lower())

    home_name_lc = str(home_team_name or "").strip().lower()
    away_name_lc = str(away_team_name or "").strip().lower()

    result_all: list[dict] = []
    seen_teams: set[str] = set()
    promotion: list[dict] = []
    relegation: list[dict] = []
    home_team = None
    away_team = None

    # Use the largest block's row count as total_teams (best estimate for stage calc)
    largest_block_rows = max(
        (len(b.select(".ui-table__body .ui-table__row")) for b in table_blocks),
        default=0,
    )

    for block in table_blocks:
        header = block.select_one(".ui-table__header")
        col_map = _build_col_map(header) if header else {}
        rows = block.select(".ui-table__body .ui-table__row")

        for row in rows:
            parsed = _parse_row(row, largest_block_rows, col_map=col_map)
            if not parsed:
                continue

            team_key = str(parsed.get("team") or "").strip().lower()
            # Skip duplicates — conference tables repeat division teams
            if team_key in seen_teams:
                continue
            seen_teams.add(team_key)

            result_all.append(parsed)
            title = str(parsed.get("promotion_title") or "").lower()
            if "promotion" in title or "champ" in title or "promoted" in title:
                promotion.append(parsed)
            elif "relegation" in title or "relegat" in title:
                relegation.append(parsed)

            if home_name_lc and home_name_lc in team_key:
                home_team = parsed
            if away_name_lc and away_name_lc in team_key:
                away_team = parsed

    return {
        "total_rows": len(result_all),
        "promotions": len(promotion),
        "relegations": len(relegation),
        "home_team": home_team,
        "away_team": away_team,
        "all": result_all,
    }


def _parse_rows(
    rows: list[Tag],
    total_teams: int,
    col_map: dict[str, int] | None,
    home_name_lc: str,
    away_name_lc: str,
) -> dict:
    result_all = []
    promotion = []
    relegation = []
    home_team = None
    away_team = None

    for row in rows:
        parsed = _parse_row(row, total_teams, col_map=col_map)
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
