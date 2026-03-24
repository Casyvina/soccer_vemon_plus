from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse


@dataclass(frozen=True)
class MatchRouteSet:
    match_url: str
    h2h_overall_url: str
    standings_overall_url: str
    standings_home_url: str
    standings_away_url: str

    def to_dict(self) -> dict[str, str]:
        return {
            "match_url": self.match_url,
            "h2h_overall_url": self.h2h_overall_url,
            "standings_overall_url": self.standings_overall_url,
            "standings_home_url": self.standings_home_url,
            "standings_away_url": self.standings_away_url,
        }


def _extract_mid(parsed) -> str:
    query = parse_qs(parsed.query or "")
    return (query.get("mid") or [""])[0].strip()


def _normalize_match_base(parsed) -> str:
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[0] != "match":
        raise ValueError(f"Unsupported Flashscore match URL path: {parsed.path}")

    base_parts = parts[:4]
    return "/" + "/".join(base_parts) + "/"


def _build_url(parsed, base_path: str, suffix: str, mid: str) -> str:
    path = base_path if not suffix else f"{base_path}{suffix.strip('/')}/"
    query = urlencode({"mid": mid})
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


def build_match_routes(match_url: str) -> MatchRouteSet:
    parsed = urlparse(str(match_url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid match URL: {match_url}")

    mid = _extract_mid(parsed)
    if not mid:
        raise ValueError(f"Flashscore match URL is missing ?mid=: {match_url}")

    base_path = _normalize_match_base(parsed)
    return MatchRouteSet(
        match_url=_build_url(parsed, base_path, "", mid),
        h2h_overall_url=_build_url(parsed, base_path, "h2h/overall", mid),
        standings_overall_url=_build_url(
            parsed, base_path, "standings/standings/overall", mid
        ),
        standings_home_url=_build_url(
            parsed, base_path, "standings/standings/home", mid
        ),
        standings_away_url=_build_url(
            parsed, base_path, "standings/standings/away", mid
        ),
    )
