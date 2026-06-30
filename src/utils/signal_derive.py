"""
Derive a LeagueFlux signal code from a market_match payload.

The payload is the dict stored in market_matches.payload in Supabase —
built by market_payload_builder.build_market_match().

Returns (chart, reality, h2h) where each is a string or None if the axis
cannot be determined from the available data.

    chart   : "O" | "U"   — home team above/below league midpoint
    reality : "BS" | "BR" | "HR" | "AR"  — last-result state of both teams
    h2h     : "H" | "D" | "A"  — H2H historical advantage

Mirrors src/lib/markets/filterUtils.ts:deriveSignalCode() in LeagueFlux.
"""
from __future__ import annotations


def derive_signal_code(payload: dict) -> tuple[str | None, str | None, str | None]:
    """
    Returns (chart, reality, h2h). Any axis may be None if data is missing.
    """
    filter_fields = payload.get("filterFields") or {}
    standings = ((payload.get("standings") or [{}])[0]) if payload.get("standings") else {}

    chart = _derive_chart(filter_fields, standings)
    reality = _derive_reality(filter_fields)
    h2h = _derive_h2h(filter_fields)

    return chart, reality, h2h


def signal_complete(chart: str | None, reality: str | None, h2h: str | None) -> bool:
    return bool(chart and reality and h2h)


def combo_key(chart: str, reality: str, h2h: str) -> str:
    return f"{chart}|{reality}|{h2h}"


# ── axis derivation ───────────────────────────────────────────────────────────

def _derive_chart(filter_fields: dict, standings: dict) -> str | None:
    # homeCP is the home team's league position (string)
    try:
        h_pos = int(str(filter_fields.get("homeCP") or "").strip())
    except (ValueError, TypeError):
        return None

    try:
        total = int(str(standings.get("totalTeams") or "").strip())
    except (ValueError, TypeError):
        return None

    if h_pos <= 0 or total <= 0:
        return None

    # O = home is in the top half (position <= midpoint), U = bottom half
    midpoint = total / 2
    return "O" if h_pos <= midpoint else "U"


def _derive_reality(filter_fields: dict) -> str | None:
    home_last = str(filter_fields.get("homeLastStatus") or "").strip().upper()
    away_last = str(filter_fields.get("awayLastStatus") or "").strip().upper()

    if not home_last or not away_last:
        return None

    home_reset = home_last in ("D", "L")
    away_reset = away_last in ("D", "L")

    if home_reset and away_reset:
        return "BR"
    if not home_reset and not away_reset:
        return "BS"
    if home_reset and not away_reset:
        return "HR"
    if not home_reset and away_reset:
        return "AR"

    return None


def _derive_h2h(filter_fields: dict) -> str | None:
    # h2hLastStatus is already normalised to "H" / "D" / "A" by the payload builder
    h2h = str(filter_fields.get("h2hLastStatus") or "").strip().upper()
    return h2h if h2h in ("H", "D", "A") else None
