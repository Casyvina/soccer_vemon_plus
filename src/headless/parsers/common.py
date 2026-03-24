from __future__ import annotations

import re
from bs4 import Tag
from urllib.parse import urljoin


def clean_team_name(name: str) -> str:
    return re.sub(r"\s*\([^)]*\)", "", str(name or "")).strip()


def text_or_empty(node: Tag | None) -> str:
    if node is None:
        return ""
    return node.get_text(" ", strip=True)


def attr_or_empty(node: Tag | None, attr_name: str) -> str:
    if node is None:
        return ""
    value = node.get(attr_name)
    return str(value or "").strip()


def absolute_url(base_url: str, href: str) -> str:
    return urljoin(str(base_url or "").strip(), str(href or "").strip())


def safe_int(value) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return 0


def parse_goals(goals_text: str) -> tuple[int, int]:
    text = str(goals_text or "").strip()
    parts = re.split(r"\s*[:\-]\s*", text)
    if len(parts) >= 2:
        return safe_int(parts[0]), safe_int(parts[1])
    return 0, 0


def parse_form_icons(form_cell: Tag | None) -> list[str]:
    if form_cell is None:
        return []

    selectors = [
        '[data-testid^="wcl-badgeForm"] span',
        ".wcl-badgeform_AKaAR span",
    ]
    values: list[str] = []
    for selector in selectors:
        for node in form_cell.select(selector):
            text = text_or_empty(node).upper().strip()
            if text:
                values.append(text)
        if values:
            return values

    for node in form_cell.find_all("span"):
        text = text_or_empty(node).upper().strip()
        if text in {"W", "D", "L", "?", "-"}:
            values.append(text)
    return values


def extract_competition_stage(full_competition: str) -> tuple[str, str]:
    stage_patterns = [
        r"Round\s*\d+",
        r"Group\s*[A-Z]",
        r"Group\s*Stage",
        r"Knockout\s*Stage?",
        r"Playoffs?",
        r"Finals?",
        r"Semi[-\s]*Finals?",
        r"Quarter[-\s]*Finals?",
        r"Club\s*Friendly",
    ]
    combined_regex = "(" + "|".join(stage_patterns) + ")"
    text = str(full_competition or "").strip()
    match = re.search(combined_regex, text, re.IGNORECASE)
    if match:
        stage = match.group(0).strip()
        competition = text.replace(stage, "").strip(" -")
        return competition, stage
    return text, ""
