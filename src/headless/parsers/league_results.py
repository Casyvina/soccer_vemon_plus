from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from headless.parsers.common import absolute_url, attr_or_empty, text_or_empty


def _pretty_slug(value: str) -> str:
    text = str(value or "").strip().replace("_", "-").strip("-")
    if not text:
        return ""
    return " ".join(part.capitalize() for part in text.split("-") if part)


def derive_context_from_results_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(str(url or ""))
        parts = [part for part in parsed.path.split("/") if part]
        if "football" in parts:
            idx = parts.index("football")
            country = _pretty_slug(parts[idx + 1]) if len(parts) > idx + 1 else ""
            competition = _pretty_slug(parts[idx + 2]) if len(parts) > idx + 2 else ""
            return country, competition
    except Exception:
        pass
    return "", ""


def normalize_results_url(url: str) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""

    cleaned = raw.replace("\\", "/").split("#", 1)[0].strip()
    if cleaned.startswith("www."):
        cleaned = f"https://{cleaned}"
    if not re.match(r"^https?://", cleaned, flags=re.IGNORECASE):
        cleaned = f"https://{cleaned.lstrip('/')}"

    base = cleaned.split("?", 1)[0].rstrip("/")
    base = re.sub(
        r"/(results|fixtures|standings|table|odds)(?:/.*)?$",
        "",
        base,
        flags=re.IGNORECASE,
    )
    return f"{base}/results/"


def build_fixtures_url(results_url: str) -> str:
    normalized = normalize_results_url(results_url)
    if not normalized:
        return ""
    return normalized[:-len("/results/")] + "/fixtures/"


def parse_league_header(html: str, page_url: str) -> dict[str, str]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    competition = text_or_empty(soup.select_one(".heading__name"))
    season = text_or_empty(soup.select_one(".heading__info"))

    breadcrumb_candidates = [
        text_or_empty(node)
        for node in soup.select("h2.breadcrumb a.breadcrumb__link")
        if text_or_empty(node)
    ]
    if not breadcrumb_candidates:
        breadcrumb_candidates = [
            text_or_empty(node)
            for node in soup.select("nav[data-testid='wcl-breadcrumbs'] li span")
            if text_or_empty(node)
        ]
    country = breadcrumb_candidates[-1] if breadcrumb_candidates else ""

    logo_node = soup.select_one(".heading__logo")
    if logo_node and logo_node.name != "img":
        logo_node = logo_node.select_one("img") or logo_node
    logo_url = attr_or_empty(logo_node, "src")

    url_country, url_competition = derive_context_from_results_url(page_url)
    if not country:
        country = url_country
    if not competition:
        competition = url_competition
    if not season:
        season = "unknown"

    return {
        "competition": competition or "unknown",
        "season": season or "unknown",
        "country": country or "unknown",
        "logo_url": logo_url,
        "page_url": page_url,
    }


def _parse_league_context(block: Tag, header: dict[str, str]) -> tuple[str, str]:
    competition = text_or_empty(block.select_one(".headerLeague__title-text"))
    if not competition:
        title_link = block.select_one("a.headerLeague__title")
        competition = attr_or_empty(title_link, "title")

    country = text_or_empty(block.select_one(".headerLeague__category-text"))
    if not competition:
        competition = str(header.get("competition") or "").strip()
    if not country:
        country = str(header.get("country") or "").strip()
    return competition, country


def _parse_round_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    return re.sub(r"^\s*Round\s*", "", raw, flags=re.IGNORECASE).strip()


def _parse_score_values(match_el: Tag) -> tuple[str, str]:
    scores = [text_or_empty(node) for node in match_el.select(".event__score")]
    if len(scores) >= 2:
        return scores[0], scores[1]
    if len(scores) == 1:
        packed = str(scores[0] or "").strip()
        score_match = re.search(r"(\d+)\s*[-:]\s*(\d+)", packed)
        if score_match:
            return score_match.group(1), score_match.group(2)
    return "", ""


def _parse_match_row(
    match_el: Tag,
    *,
    index: int,
    round_no: str,
    competition_name: str,
    country_name: str,
    page_url: str,
    phase: str,
) -> list[str] | None:
    try:
        time_text = text_or_empty(match_el.select_one(".event__time"))
        parts = [part for part in time_text.split(" ") if part]
        date_part = parts[0] if parts else ""
        time_part = parts[1] if len(parts) > 1 else ""

        link = match_el.select_one("a.eventRowLink")
        match_url = absolute_url(page_url, attr_or_empty(link, "href"))
        match_id_match = re.search(r"mid=([a-zA-Z0-9]{8})", match_url)
        match_id = match_id_match.group(1) if match_id_match else ""
        if not match_id:
            return None

        home_root = match_el.select_one(".event__homeParticipant")
        away_root = match_el.select_one(".event__awayParticipant")
        home_name = text_or_empty(home_root.select_one("span") if home_root else None)
        away_name = text_or_empty(away_root.select_one("span") if away_root else None)
        home_icon = attr_or_empty(home_root.select_one("img") if home_root else None, "src")
        away_icon = attr_or_empty(away_root.select_one("img") if away_root else None, "src")
        home_goal, away_goal = _parse_score_values(match_el)

        return [
            index,
            round_no,
            date_part,
            time_part,
            home_name,
            away_name,
            home_goal,
            away_goal,
            match_url,
            home_icon,
            away_icon,
            match_id,
            0,
            competition_name,
            country_name,
            phase,
        ]
    except Exception:
        return None


def parse_league_rows(
    html: str,
    *,
    header: dict[str, str],
    page_url: str,
    phase: str,
    start_index: int = 1,
) -> list[list[str]]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    blocks = soup.select("div.headerLeague__wrapper, div.event__round, div.event__match")

    rows: list[list[str]] = []
    current_round = None
    current_competition = str(header.get("competition") or "").strip()
    current_country = str(header.get("country") or "").strip()
    index = int(start_index)

    for block in blocks:
        classes = " ".join(block.get("class") or [])
        if "headerLeague__wrapper" in classes:
            current_competition, current_country = _parse_league_context(block, header)
            current_round = None
            continue
        if "event__round" in classes:
            current_round = _parse_round_text(text_or_empty(block))
            continue
        if "event__match" not in classes:
            continue

        row = _parse_match_row(
            block,
            index=index,
            round_no=str(current_round or ""),
            competition_name=current_competition,
            country_name=current_country,
            page_url=page_url,
            phase=phase,
        )
        if row:
            rows.append(row)
            index += 1

    return rows
