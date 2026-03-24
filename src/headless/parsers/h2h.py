from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from headless.parsers.common import absolute_url, text_or_empty


def parse_h2h_row(row: Tag, source_url: str) -> dict[str, str] | None:
    if row is None:
        return None

    score_nodes = row.select(".h2h__result span")
    score_home = text_or_empty(score_nodes[0]) if len(score_nodes) > 0 else ""
    score_away = text_or_empty(score_nodes[1]) if len(score_nodes) > 1 else ""

    badge = text_or_empty(row.select_one('div[data-testid^="wcl-badgeForm"] span'))
    href = row.get("href")

    return {
        "date": text_or_empty(row.select_one(".h2h__date")),
        "event": text_or_empty(row.select_one(".h2h__event span:nth-of-type(2)")),
        "home": text_or_empty(
            row.select_one(".h2h__homeParticipant .h2h__participantInner")
        ),
        "away": text_or_empty(
            row.select_one(".h2h__awayParticipant .h2h__participantInner")
        ),
        "score_home": score_home,
        "score_away": score_away,
        "result": badge,
        "url": absolute_url(source_url, str(href or "").strip()),
    }


def parse_h2h_sections(html: str, source_url: str = "") -> list[dict]:
    soup = BeautifulSoup(str(html or ""), "html.parser")
    sections = soup.select(".h2h__section.section, .h2h__section")

    parsed_sections: list[dict] = []
    for section in sections:
        title = text_or_empty(
            section.select_one("[data-testid='wcl-scores-overline-02']")
        )

        matches = []
        for row in section.select(".rows a.h2h__row"):
            parsed_row = parse_h2h_row(row, source_url=source_url)
            if parsed_row:
                matches.append(parsed_row)

        if not title and not matches:
            continue

        parsed_sections.append(
            {
                "section_title": title,
                "matches": matches,
            }
        )

    return parsed_sections
