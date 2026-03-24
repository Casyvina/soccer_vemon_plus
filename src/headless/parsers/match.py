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


def parse_match_details(soup: BeautifulSoup) -> dict[str, str]:
    start_node = soup.select_one("div.duelParticipant__startTime div")
    dt_raw = text_or_empty(start_node)
    if " " in dt_raw:
        date_text, time_text = dt_raw.split(" ", 1)
    else:
        date_text, time_text = "", ""

    return {
        "date": date_text.strip(),
        "time": time_text.strip(),
        "home_team": _extract_team_name(soup, "home"),
        "away_team": _extract_team_name(soup, "away"),
    }


def parse_match_page(html: str) -> dict[str, object]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    return {
        "breadcrumb": parse_breadcrumb_info(soup),
        "infobox": parse_infobox_text(soup),
        "match_details": parse_match_details(soup),
    }
