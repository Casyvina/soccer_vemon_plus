from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from headless.parsers.common import (
    absolute_url,
    attr_or_empty,
    clean_team_name,
    text_or_empty,
)
from utils.all_odds_store import extract_match_id, infer_iso_date


def parse_selected_day_label(html: str) -> str:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    return text_or_empty(soup.select_one("[data-testid='wcl-dayPickerButton']"))


def infer_selected_date_iso(
    html: str,
    *,
    now: datetime | None = None,
) -> str:
    label = parse_selected_day_label(html)
    return infer_selected_date_iso_from_label(label, now=now)


def infer_selected_date_iso_from_label(
    label: str,
    *,
    now: datetime | None = None,
) -> str:
    text = str(label or "").strip()
    match = re.search(r"(\d{1,2})[/-](\d{1,2})\s+([A-Za-z]{2})", text)
    if not match:
        return "unknown"

    day_month = f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"
    day_code = match.group(3).upper()
    return infer_iso_date(day_month, day_code, now=now)


def parse_odds_match_rows(html: str, *, page_url: str = "") -> list[dict]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    container = soup.select_one("section.event.odds .sportName.soccer")
    if container is None:
        return []

    current_competition = ""
    current_country = ""
    matches: list[dict] = []

    for child in container.find_all(recursive=False):
        if not isinstance(child, Tag):
            continue

        classes = set(child.get("class") or [])
        if "headerLeague__wrapper" in classes:
            current_competition, current_country = _parse_header_context(child)
            continue
        if "event__match" not in classes:
            continue

        item = _parse_match_row(
            child,
            page_url=page_url,
            competition=current_competition,
            country=current_country,
        )
        if item:
            matches.append(item)

    return matches


def build_all_odds_snapshot(
    html: str,
    *,
    page_url: str = "",
    day_offset: int = 0,
    date_iso: str | None = None,
    now: datetime | None = None,
) -> dict:
    resolved_date = str(date_iso or "").strip() or infer_selected_date_iso(
        html, now=now
    )
    selected_day_label = parse_selected_day_label(html)
    matches = {
        str(item["match_id"]): item
        for item in parse_odds_match_rows(html, page_url=page_url)
        if str(item.get("match_id") or "").strip()
    }

    return {
        "schema": 1,
        "date": resolved_date or "unknown",
        "day_offset": int(day_offset),
        "selected_day_label": selected_day_label,
        "page_url": str(page_url or "").strip(),
        "matches": matches,
    }


def _parse_header_context(block: Tag) -> tuple[str, str]:
    competition = text_or_empty(block.select_one(".headerLeague__title-text"))
    if not competition:
        competition = attr_or_empty(block.select_one(".headerLeague__title"), "title")

    country = text_or_empty(block.select_one(".headerLeague__category-text"))
    return competition, country


def _parse_match_row(
    match_el: Tag,
    *,
    page_url: str,
    competition: str,
    country: str,
) -> dict | None:
    link = match_el.select_one("a.eventRowLink")
    url = absolute_url(page_url, attr_or_empty(link, "href"))
    match_id = extract_match_id(url)
    if not match_id:
        return None

    home = clean_team_name(
        text_or_empty(match_el.select_one(".event__participant--home"))
    )
    away = clean_team_name(
        text_or_empty(match_el.select_one(".event__participant--away"))
    )
    time_text, status = _parse_time_and_status(match_el)
    odds = _parse_main_odds(match_el)
    scores = _parse_row_scores(match_el, score_state=status)

    return {
        "match_id": match_id,
        "time": time_text,
        "url": url,
        "home": home,
        "away": away,
        "competition": str(competition or "").strip(),
        "country": str(country or "").strip(),
        "status": str(status or "").strip(),
        "odds": odds,
        "scores": scores,
        "details_fetched": False,
    }


def _parse_time_and_status(match_el: Tag) -> tuple[str, str]:
    time_text = text_or_empty(match_el.select_one(".event__time"))
    score_state = attr_or_empty(
        match_el.select_one("[data-testid='wcl-matchRowScore']"),
        "data-state",
    )
    if time_text:
        return time_text, score_state or "scheduled"

    stage_values: list[str] = []
    for node in match_el.select(".event__stage--block, .event__stage--pkv"):
        text = text_or_empty(node).replace("\xa0", " ").strip()
        if text:
            stage_values.append(text)

    if stage_values:
        return " ".join(stage_values), score_state or "unknown"
    if score_state:
        return score_state.upper(), score_state
    return "", ""


def _parse_main_odds(match_el: Tag) -> dict[str, str]:
    odds = {
        "1": "",
        "X": "",
        "2": "",
        "1b": "",
        "Xb": "",
        "2b": "",
    }

    for odd_node in match_el.select(".event__odds .odds__odd"):
        classes = set(odd_node.get("class") or [])
        key = ""
        if "event__odd--odd1" in classes:
            key = "1"
        elif "event__odd--odd2" in classes:
            key = "X"
        elif "event__odd--odd3" in classes:
            key = "2"

        if not key:
            continue

        value = text_or_empty(odd_node.select_one("span")) or text_or_empty(odd_node)
        value = "" if value == "-" else value
        odds[key] = value

    return odds


def _parse_row_scores(match_el: Tag, *, score_state: str = "") -> dict[str, int | str]:
    score_nodes = match_el.select("[data-testid='wcl-matchRowScore']")
    if not score_nodes:
        return {}

    home_text = ""
    away_text = ""
    node_state = ""

    for node in score_nodes:
        side = attr_or_empty(node, "data-side")
        value = text_or_empty(node)
        if side == "1" and not home_text:
            home_text = value
        elif side == "2" and not away_text:
            away_text = value

        if not node_state:
            node_state = attr_or_empty(node, "data-state")

    if not home_text or not away_text:
        return {}

    scores: dict[str, int | str] = {
        "ft_home": _coerce_score_value(home_text),
        "ft_away": _coerce_score_value(away_text),
    }

    resolved_state = str(node_state or score_state or "").strip().lower()
    if resolved_state:
        scores["state"] = resolved_state

    return scores


def _coerce_score_value(value: str) -> int | str:
    text = str(value or "").strip()
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return text
