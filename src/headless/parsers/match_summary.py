from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from headless.parsers.common import text_or_empty


def _is_goal_incident(row: Tag) -> bool:
    for svg in row.select("svg"):
        testid = (svg.get("data-testid") or "").lower()
        classes = " ".join(svg.get("class") or []).lower()
        title_node = svg.select_one("title")
        title = text_or_empty(title_node).lower()
        combined = f"{testid} {classes} {title}"
        if any(t in combined for t in ("goal", "soccer", "penalty", "owngoal", "own goal")):
            return True
    home = text_or_empty(row.select_one(".smv__incidentHomeScore"))
    away = text_or_empty(row.select_one(".smv__incidentAwayScore"))
    return bool(home or away)


def parse_half_scores(html: str) -> dict[str, str]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    result: dict[str, str] = {}
    for header in soup.select(
        "div.wclHeaderSection--summary[data-testid='wcl-headerSection-text']"
    ):
        spans = header.select('span[data-testid="wcl-scores-overline-02"]')
        if len(spans) < 2:
            continue
        label = text_or_empty(spans[0]).lower()
        score = text_or_empty(spans[1])
        if "1st half" in label:
            result["1st_half"] = score
        elif "2nd half" in label:
            result["2nd_half"] = score
    return result


def parse_goal_events(html: str) -> list[dict]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    goals = []
    for row in soup.select(".smv__participantRow"):
        if not _is_goal_incident(row):
            continue
        classes = " ".join(row.get("class") or [])
        side = "H" if "smv__homeParticipant" in classes else "A"
        minute = text_or_empty(row.select_one(".smv__timeBox")).replace("'", "").strip()
        player = text_or_empty(row.select_one(".smv__playerName"))

        home_score = text_or_empty(row.select_one(".smv__incidentHomeScore"))
        away_score = text_or_empty(row.select_one(".smv__incidentAwayScore"))
        if home_score and away_score:
            score_after = f"{home_score}-{away_score}"
        elif home_score or away_score:
            # Only one container present — it already contains the full "H - A" score
            score_after = home_score or away_score
        else:
            m = re.search(
                r"(\d+)\s*[-:]\s*(\d+)",
                text_or_empty(row.select_one(".smv__incidentScore")),
            )
            score_after = f"{m.group(1)}-{m.group(2)}" if m else ""
        goals.append({
            "side": side,
            "player": player,
            "minute": minute or "?",
            "score_after_goal": score_after,
        })
    return goals


def _split_half_score(score_str: str) -> tuple[str, str]:
    text = str(score_str or "").strip().replace("\u2013", "-").replace("\u2014", "-")
    m = re.search(r"(\d+)\s*[-:]\s*(\d+)", text)
    return (m.group(1).strip(), m.group(2).strip()) if m else ("", "")


def parse_ft_score(html: str) -> tuple[str, str]:
    """Parse full-time score from the match header (detailScore__wrapper) on the summary page."""
    soup = BeautifulSoup(str(html or ""), "html.parser")
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


def parse_match_summary(html: str) -> dict:
    """Parse a rendered match summary page — returns halftime scores, FT score, goal events and rhythm."""
    half_scores = parse_half_scores(html)
    goals = parse_goal_events(html)
    h1, a1 = _split_half_score(half_scores.get("1st_half", ""))
    h2, a2 = _split_half_score(half_scores.get("2nd_half", ""))
    ft_home, ft_away = parse_ft_score(html)
    # Fallback: compute FT from halves when header score is absent
    if not ft_home and h1 and h2:
        try:
            ft_home = str(int(h1) + int(h2))
        except Exception:
            pass
    if not ft_away and a1 and a2:
        try:
            ft_away = str(int(a1) + int(a2))
        except Exception:
            pass
    rhythm = "".join(g.get("side", "") for g in goals if g.get("side") in {"H", "A"})
    return {
        "1h_home": h1,
        "1h_away": a1,
        "2h_home": h2,
        "2h_away": a2,
        "ft_home": ft_home,
        "ft_away": ft_away,
        "goal_rhythm": rhythm,
        "goal_events": goals,
    }
