from __future__ import annotations

from bs4 import BeautifulSoup

from headless.parsers.common import (
    attr_or_empty,
    clean_team_name,
    extract_competition_stage,
    text_or_empty,
)


def parse_breadcrumb_info(soup: BeautifulSoup) -> dict[str, str]:
    country = text_or_empty(
        soup.select_one("nav[data-testid='wcl-breadcrumbs'] li:nth-of-type(2) span")
    )
    competition_node = soup.select_one(
        "nav[data-testid='wcl-breadcrumbs'] li:nth-of-type(3) span"
    )
    full_competition = text_or_empty(competition_node)
    competition_link = soup.select_one(
        "nav[data-testid='wcl-breadcrumbs'] li:nth-of-type(3) a"
    )

    competition, stage = extract_competition_stage(full_competition)
    return {
        "country": country,
        "competition": competition,
        "stage": stage,
        "competition_url": attr_or_empty(competition_link, "href"),
    }


def parse_infobox_text(soup: BeautifulSoup) -> str:
    return text_or_empty(
        soup.select_one(
            "div.infoBox__wrapper.infoBoxModule div.infoBox__info"
        )
    )


def _extract_team_name(soup: BeautifulSoup, side: str) -> str:
    anchor = soup.select_one(
        f"div.duelParticipant__{side} "
        "div.participant__participantName a"
    )
    if anchor:
        return clean_team_name(text_or_empty(anchor))

    image = soup.select_one(
        f"div.duelParticipant__{side} img.participant__image"
    )
    return clean_team_name(attr_or_empty(image, "alt"))


_TERMINAL_STATUS_LABELS = frozenset({
    "finished", "ft", "aet", "ap", "after extra time",
    "after penalties", "awarded", "walkover", "cancelled",
    "postponed", "abandoned",
})


def parse_match_score(soup: BeautifulSoup) -> tuple[str, str]:
    wrapper = soup.select_one(".detailScore__wrapper")
    if not wrapper:
        return "", ""
    spans = [
        s for s in wrapper.find_all("span", recursive=False)
        if "divider" not in " ".join(s.get("class") or [])
    ]
    if len(spans) >= 2:
        return spans[0].get_text(strip=True), spans[1].get_text(strip=True)
    return "", ""


def parse_match_status(soup: BeautifulSoup) -> dict:
    label = text_or_empty(soup.select_one(".detailScore__status"))
    normalized = label.lower().strip()
    is_terminal = normalized in _TERMINAL_STATUS_LABELS
    return {"label": label, "normalized": normalized, "is_terminal": is_terminal}


def parse_match_details(soup: BeautifulSoup) -> dict[str, str]:
    start_node = soup.select_one("div.duelParticipant__startTime div")
    dt_raw = text_or_empty(start_node)
    if " " in dt_raw:
        date_text, time_text = dt_raw.split(" ", 1)
    else:
        date_text, time_text = "", ""

    score_home, score_away = parse_match_score(soup)
    return {
        "date": date_text.strip(),
        "time": time_text.strip(),
        "home_team": _extract_team_name(soup, "home"),
        "away_team": _extract_team_name(soup, "away"),
        "score_home": score_home,
        "score_away": score_away,
    }


def parse_match_page(html: str) -> dict[str, object]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    return {
        "breadcrumb": parse_breadcrumb_info(soup),
        "infobox": parse_infobox_text(soup),
        "match_details": parse_match_details(soup),
        "match_status": parse_match_status(soup),
    }
