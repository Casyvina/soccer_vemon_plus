from __future__ import annotations

import json
import re
import time
from collections import OrderedDict

from dataclasses import dataclass
from datetime import datetime

from headless.http import HeadlessHttpClient
from headless.parsers.h2h import parse_h2h_sections
from headless.parsers.match import parse_match_page
from headless.parsers.match_summary import parse_match_summary
from headless.parsers.standings import parse_standings_page
from headless.routes import MatchRouteSet, build_match_routes
from utils.all_odds_store import extract_match_id
from utils.file_saver import save_raw_match_json
from utils.paths import resolve_processed_dir


@dataclass
class MatchPipelineResult:
    match_id: str
    routes: dict[str, str]
    html_paths: dict[str, str]
    json_path: str
    payload: dict


class MatchPipeline:
    def __init__(
        self,
        config=None,
        client: HeadlessHttpClient | None = None,
        page_source_fetcher=None,
        cache_enabled: bool = True,
        max_cache_entries: int = 60,
        reset_cache_per_match: bool = False,
    ):
        self.config = config
        self.client = client or HeadlessHttpClient()
        self.page_source_fetcher = page_source_fetcher
        self.cache_enabled = bool(cache_enabled)
        self.max_cache_entries = max(0, int(max_cache_entries))
        self.reset_cache_per_match = bool(reset_cache_per_match)
        self._page_cache: OrderedDict[str, str] = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0

    def run_from_url(
        self,
        match_url: str,
        *,
        save_html: bool = True,
        save_json: bool = True,
    ) -> MatchPipelineResult:
        if self.reset_cache_per_match:
            self.reset_cache()
        routes = build_match_routes(match_url)

        t0 = time.time()
        html_pages = self._fetch_route_pages(routes)
        print(f"  Base pages: {time.time() - t0:.1f}s")

        match_block = parse_match_page(html_pages["match"])
        breadcrumb = match_block.get("breadcrumb") or {}
        infobox = str(match_block.get("infobox") or "")
        match_details = match_block.get("match_details") or {}
        match_status = match_block.get("match_status") or {
            "label": "", "normalized": "", "is_terminal": False
        }

        home_team = str(match_details.get("home_team") or "")
        away_team = str(match_details.get("away_team") or "")

        if not home_team or not away_team:
            raise ValueError(
                f"Match page returned no team names — likely live or page did not render: {match_url}"
            )

        print(f"  Match: {home_team} vs {away_team} | {breadcrumb.get('competition','')} {breadcrumb.get('stage','')}")

        h2h_sections = parse_h2h_sections(
            html_pages["h2h_overall"], source_url=routes.h2h_overall_url
        )
        h2h_count = sum(len(s.get("matches") or []) for s in h2h_sections)
        print(f"  H2H: {len(h2h_sections)} sections, {h2h_count} matches")

        standings_overall = parse_standings_page(
            html_pages["standings_overall"], home_team, away_team
        )
        print(f"  Standings: {standings_overall.get('total_rows', 0)} teams")

        t1 = time.time()
        supplemental_pages = self._fetch_named_pages(
            self._build_supplemental_requests(h2h_sections)
        )
        print(f"  Supplemental: {len(supplemental_pages)} pages — {time.time() - t1:.1f}s")

        summary_requests = self._collect_summary_requests(h2h_sections, home_team, away_team)
        t2 = time.time()
        summary_pages = self._fetch_summary_pages(summary_requests)
        summaries = self._parse_summaries(summary_pages, summary_requests)
        print(f"  Summaries: {len(summaries)} parsed — {time.time() - t2:.1f}s")

        payload = {
            "url": routes.match_url,
            "match_details": match_details,
            "match_status": match_status,
            "breadcrumb": breadcrumb,
            "infobox": infobox,
            "odds": {},
            "h2h": self._embed_summaries_in_h2h(h2h_sections, summaries),
            "table": standings_overall,
            "last_matches": self._build_last_matches(
                h2h_sections, supplemental_pages, summaries
            ),
            "h2h_standings": self._build_h2h_standings(
                h2h_sections, supplemental_pages, summaries
            ),
        }

        date_iso = self._parse_date_text_to_iso(match_details.get("date"))
        match_id = extract_match_id(routes.match_url)
        html_paths = (
            self._save_html_snapshots(html_pages, match_id=match_id, date_iso=date_iso)
            if save_html
            else {}
        )

        json_path = ""
        if save_json:
            json_path = save_raw_match_json(
                payload,
                timestamp=False,
                config=self.config,
                date_iso=date_iso,
                match_id=match_id,
            )

        return MatchPipelineResult(
            match_id=match_id,
            routes=routes.to_dict(),
            html_paths=html_paths,
            json_path=json_path,
            payload=payload,
        )

    def _fetch_route_pages(self, routes: MatchRouteSet) -> dict[str, str]:
        return self._fetch_named_pages(
            [
                ("match", routes.match_url),
                ("h2h_overall", routes.h2h_overall_url),
                ("standings_overall", routes.standings_overall_url),
            ]
        )

    def _fetch_named_pages(self, items: list[tuple[str, str]]) -> dict[str, str]:
        pages: dict[str, str] = {}
        uncached_by_url: dict[str, list[str]] = {}

        for key, url in items:
            cached = self._get_cached_page(url)
            if cached is not None:
                pages[key] = cached
                continue
            uncached_by_url.setdefault(url, []).append(key)

        if uncached_by_url:
            representatives = [
                (keys[0], url) for url, keys in uncached_by_url.items() if keys
            ]
            fetched = self._fetch_uncached_pages(representatives)
            for url, keys in uncached_by_url.items():
                representative_key = keys[0]
                html = str(fetched.get(representative_key) or "")
                self._cache_page(url, html)
                for key in keys:
                    pages[key] = html

        return pages

    def reset_cache(self) -> None:
        self._page_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0

    def cache_stats(self) -> dict[str, int]:
        return {
            "enabled": int(self.cache_enabled),
            "entries": len(self._page_cache),
            "max_entries": self.max_cache_entries,
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "evictions": self._cache_evictions,
        }

    def _fetch_uncached_pages(self, items: list[tuple[str, str]]) -> dict[str, str]:
        if self.page_source_fetcher is not None:
            fetch_urls = getattr(self.page_source_fetcher, "fetch_urls", None)
            if callable(fetch_urls):
                return fetch_urls(items)
            fetch_url = getattr(self.page_source_fetcher, "fetch_url", None)
            if callable(fetch_url):
                return {key: fetch_url(url, key=key) for key, url in items}
        return {key: self.client.fetch_text(url) for key, url in items}

    def _get_cached_page(self, url: str) -> str | None:
        if not self.cache_enabled or self.max_cache_entries <= 0:
            self._cache_misses += 1
            return None

        cached = self._page_cache.get(url)
        if cached is None:
            self._cache_misses += 1
            return None

        self._page_cache.move_to_end(url)
        self._cache_hits += 1
        return cached

    def _cache_page(self, url: str, html: str) -> None:
        if not self.cache_enabled or self.max_cache_entries <= 0:
            return

        if url in self._page_cache:
            self._page_cache.move_to_end(url)
            self._page_cache[url] = html
            return

        self._page_cache[url] = html
        if len(self._page_cache) > self.max_cache_entries:
            self._page_cache.popitem(last=False)
            self._cache_evictions += 1

    # ------------------------------------------------------------------
    # Supplemental requests (last-match h2h + standings, h2h standings)
    # ------------------------------------------------------------------

    def _build_supplemental_requests(
        self, h2h_sections: list[dict]
    ) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []

        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            matches = [
                m for m in (section.get("matches") or []) if isinstance(m, dict)
            ]
            if title.upper().startswith("LAST MATCHES:") and matches:
                team_name = title.split(":", 1)[-1].strip()
                if not team_name:
                    continue
                match_url = str(matches[0].get("url") or "").strip()
                if not match_url:
                    continue
                try:
                    routes = build_match_routes(match_url)
                except Exception:
                    continue
                items.append((self._last_h2h_key(team_name), routes.h2h_overall_url))
                items.append(
                    (self._last_standings_key(team_name), routes.standings_overall_url)
                )

        h2h_section = next(
            (
                s
                for s in h2h_sections
                if "HEAD-TO-HEAD" in str(s.get("section_title") or "").upper()
            ),
            None,
        )
        if not h2h_section:
            return items

        for match_item in [
            m for m in (h2h_section.get("matches") or []) if isinstance(m, dict)
        ][:5]:
            match_url = str(match_item.get("url") or "").strip()
            match_id = extract_match_id(match_url)
            if not match_id:
                continue
            try:
                routes = build_match_routes(match_url)
            except Exception:
                continue
            items.append(
                (self._h2h_standings_key(match_id), routes.standings_overall_url)
            )

        return items

    # ------------------------------------------------------------------
    # Summary page fetching (Selenium — halftime scores + goal rhythm)
    # ------------------------------------------------------------------

    @staticmethod
    def _summary_url(match_url: str) -> str:
        try:
            return build_match_routes(match_url).summary_url
        except Exception:
            return ""

    def _collect_summary_requests(
        self,
        h2h_sections: list[dict],
        main_home_team: str,
        main_away_team: str,
    ) -> list[dict]:
        requests: list[dict] = []
        seen_urls: set[str] = set()

        # Last 1 match per team (home + away LAST MATCHES sections)
        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            if not title.upper().startswith("LAST MATCHES:"):
                continue
            section_team = title.split(":", 1)[-1].strip()
            matches = [
                m for m in (section.get("matches") or []) if isinstance(m, dict)
            ]
            if not matches:
                continue
            match_item = matches[0]
            match_url = str(match_item.get("url") or "").strip()
            mid = extract_match_id(match_url)
            if not mid or not match_url:
                continue
            summary_url = self._summary_url(match_url)
            if summary_url in seen_urls:
                continue
            seen_urls.add(summary_url)

            source_home = str(match_item.get("home") or "")
            source_away = str(match_item.get("away") or "")
            # perspective: section_team is always "home" (tracked team)
            persp_away = (
                source_away
                if section_team.strip().lower() == source_home.strip().lower()
                else source_home
            )
            ft_h = str(match_item.get("score_home") or "")
            ft_a = str(match_item.get("score_away") or "")
            requests.append({
                "key": f"summary_last_{mid}",
                "summary_url": summary_url,
                "zero_zero": self._is_zero_zero(ft_h, ft_a),
                "match_id": mid,
                "match_url": match_url,
                "source_home": source_home,
                "source_away": source_away,
                "perspective_home": section_team,
                "perspective_away": persp_away,
                "ft_source_home": ft_h,
                "ft_source_away": ft_a,
            })

        # Last 3 H2H matches — perspective = main match home/away teams
        h2h_section = next(
            (
                s
                for s in h2h_sections
                if "HEAD-TO-HEAD" in str(s.get("section_title") or "").upper()
            ),
            None,
        )
        if h2h_section:
            for match_item in [
                m for m in (h2h_section.get("matches") or []) if isinstance(m, dict)
            ][:3]:
                match_url = str(match_item.get("url") or "").strip()
                mid = extract_match_id(match_url)
                if not mid or not match_url:
                    continue
                summary_url = self._summary_url(match_url)
                if summary_url in seen_urls:
                    continue
                seen_urls.add(summary_url)
                source_home = str(match_item.get("home") or "")
                source_away = str(match_item.get("away") or "")
                ft_h = str(match_item.get("score_home") or "")
                ft_a = str(match_item.get("score_away") or "")
                requests.append({
                    "key": f"summary_h2h_{mid}",
                    "summary_url": summary_url,
                    "zero_zero": self._is_zero_zero(ft_h, ft_a),
                    "match_id": mid,
                    "match_url": match_url,
                    "source_home": source_home,
                    "source_away": source_away,
                    "perspective_home": main_home_team,
                    "perspective_away": main_away_team,
                    "ft_source_home": ft_h,
                    "ft_source_away": ft_a,
                })

        return requests

    def _fetch_summary_pages(self, requests: list[dict]) -> dict[str, str]:
        if not requests or self.page_source_fetcher is None:
            return {}
        # Skip 0-0 matches — no page visit needed
        non_zero = [r for r in requests if not r.get("zero_zero") and r.get("match_url")]
        if not non_zero:
            return {}
        # Tab-clicking fetch — navigates to match_url and clicks the Summary tab
        fetch_summary = getattr(self.page_source_fetcher, "fetch_summary_pages", None)
        if callable(fetch_summary):
            items = [(r["key"], r["match_url"]) for r in non_zero]
            try:
                return fetch_summary(items)
            except Exception:
                return {}
        return {}

    def _parse_summaries(
        self,
        pages: dict[str, str],
        requests: list[dict],
    ) -> dict[str, dict]:
        """Returns {match_id: half_scores_block} with perspective applied."""
        result: dict[str, dict] = {}
        for req in requests:
            mid = req["match_id"]
            if req.get("zero_zero"):
                # 0-0 match: store zeros without visiting the summary page
                result[mid] = {
                    "match_id": mid,
                    "match_url": req["match_url"],
                    "home_team": req["perspective_home"],
                    "away_team": req["perspective_away"],
                    "source_home_team": req["source_home"],
                    "source_away_team": req["source_away"],
                    "1h_home": "0",
                    "1h_away": "0",
                    "2h_home": "0",
                    "2h_away": "0",
                    "ft_home": "0",
                    "ft_away": "0",
                    "goal_rhythm": "",
                    "goal_events": [],
                }
                continue

            html = pages.get(req["key"], "")
            if not html:
                continue
            try:
                raw = parse_match_summary(html)
            except Exception:
                continue
            block = self._build_half_scores_block(raw, req)
            # Only store if we got something useful
            if any(block.get(k) for k in ("1h_home", "1h_away", "goal_rhythm", "goal_events")):
                result[mid] = block
        return result

    @staticmethod
    def _build_half_scores_block(raw_summary: dict, req: dict) -> dict:
        """Build a half_scores dict from the tracking team's perspective."""
        source_home = req["source_home"]
        source_away = req["source_away"]
        persp_home = req["perspective_home"]
        persp_away = req["perspective_away"]
        ft_src_home = req["ft_source_home"]
        ft_src_away = req["ft_source_away"]

        # Swap when the tracking team (persp_home) was the away side in the source match
        needs_swap = persp_home.strip().lower() == source_away.strip().lower()

        def pick(h_val: str, a_val: str) -> tuple[str, str]:
            return (a_val, h_val) if needs_swap else (h_val, a_val)

        h1h, h1a = pick(
            raw_summary.get("1h_home", ""), raw_summary.get("1h_away", "")
        )
        h2h, h2a = pick(
            raw_summary.get("2h_home", ""), raw_summary.get("2h_away", "")
        )
        fth, fta = pick(ft_src_home, ft_src_away)

        rhythm = str(raw_summary.get("goal_rhythm") or "")
        if needs_swap:
            rhythm = "".join(
                "A" if c == "H" else ("H" if c == "A" else c) for c in rhythm
            )

        goal_events: list[dict] = []
        for ev in raw_summary.get("goal_events") or []:
            ev = dict(ev)
            if needs_swap and ev.get("side") in ("H", "A"):
                ev["side"] = "A" if ev["side"] == "H" else "H"
            goal_events.append(ev)

        return {
            "match_id": req["match_id"],
            "match_url": req["match_url"],
            "home_team": persp_home,
            "away_team": persp_away,
            "source_home_team": source_home,
            "source_away_team": source_away,
            "1h_home": h1h,
            "1h_away": h1a,
            "2h_home": h2h,
            "2h_away": h2a,
            "ft_home": fth,
            "ft_away": fta,
            "goal_rhythm": rhythm,
            "goal_events": goal_events,
        }

    # ------------------------------------------------------------------
    # Payload builders
    # ------------------------------------------------------------------

    def _embed_summaries_in_h2h(
        self, h2h_sections: list[dict], summaries: dict[str, dict]
    ) -> list[dict]:
        """Embed half_scores into the first match of each LAST MATCHES section."""
        if not summaries:
            return h2h_sections
        result: list[dict] = []
        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            if title.upper().startswith("LAST MATCHES:"):
                matches = list(section.get("matches") or [])
                if matches:
                    first = dict(matches[0])
                    mid = extract_match_id(str(first.get("url") or ""))
                    if mid and mid in summaries:
                        first["half_scores"] = summaries[mid]
                    matches = [first] + matches[1:]
                section = {**section, "matches": matches}
            result.append(section)
        return result

    def _build_last_matches(
        self,
        h2h_sections: list[dict],
        supplemental_pages: dict[str, str],
        summaries: dict[str, dict],
    ) -> dict:
        result: dict[str, dict] = {}
        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            if not title.upper().startswith("LAST MATCHES:"):
                continue
            team_name = title.split(":", 1)[-1].strip()
            matches = [
                m for m in (section.get("matches") or []) if isinstance(m, dict)
            ]
            if not team_name or not matches:
                continue
            result[team_name] = self._build_last_match_payload(
                team_name, matches[0], supplemental_pages, summaries
            )
        return result

    def _build_last_match_payload(
        self,
        team_name: str,
        match_item: dict,
        pages: dict[str, str],
        summaries: dict[str, dict],
    ) -> dict:
        match_url = str(match_item.get("url") or "").strip()
        home_team = str(match_item.get("home") or "").strip()
        away_team = str(match_item.get("away") or "").strip()
        mid = extract_match_id(match_url)
        half_scores = summaries.get(mid, {}) if mid else {}

        fallback = {
            "match_url": match_url,
            "h2h": [],
            "table": {},
            "odds_data": {},
            "half_scores": half_scores,
            "has_table": False,
            "has_odds": False,
        }

        if not match_url:
            return fallback

        try:
            h2h_data = parse_h2h_sections(
                pages.get(self._last_h2h_key(team_name), ""),
                source_url=self._route_url_for(match_url, "h2h_overall_url"),
            )
            table_data = parse_standings_page(
                pages.get(self._last_standings_key(team_name), ""),
                home_team,
                away_team,
            )
            has_table = self._has_standings_table(table_data)
            return {
                "match_url": match_url,
                "h2h": h2h_data,
                "table": table_data if has_table else {},
                "odds_data": {},
                "half_scores": half_scores,
                "has_table": has_table,
                "has_odds": False,
            }
        except Exception:
            return fallback

    def _build_h2h_standings(
        self,
        h2h_sections: list[dict],
        supplemental_pages: dict[str, str],
        summaries: dict[str, dict],
    ) -> dict:
        result: dict[str, dict] = {}
        h2h_section = next(
            (
                s
                for s in h2h_sections
                if "HEAD-TO-HEAD" in str(s.get("section_title") or "").upper()
            ),
            None,
        )
        if not h2h_section:
            return result

        for match_item in [
            m for m in (h2h_section.get("matches") or []) if isinstance(m, dict)
        ][:5]:
            match_url = str(match_item.get("url") or "").strip()
            match_id = extract_match_id(match_url)
            if not match_id:
                continue

            home_team = str(match_item.get("home") or "").strip()
            away_team = str(match_item.get("away") or "").strip()
            half_scores = summaries.get(match_id, {})

            fallback = {
                "match_url": match_url,
                "total_rows": 0,
                "promotions": 0,
                "relegations": 0,
                "home_team": {},
                "away_team": {},
                "odds_data": {},
                "half_scores": half_scores,
                "has_table": False,
            }

            try:
                table_data = parse_standings_page(
                    supplemental_pages.get(self._h2h_standings_key(match_id), ""),
                    home_team,
                    away_team,
                )
                result[match_id] = {
                    "match_url": match_url,
                    "total_rows": int(table_data.get("total_rows") or 0),
                    "promotions": int(table_data.get("promotions") or 0),
                    "relegations": int(table_data.get("relegations") or 0),
                    "home_team": table_data.get("home_team") or {},
                    "away_team": table_data.get("away_team") or {},
                    "odds_data": {},
                    "half_scores": half_scores,
                    "has_table": self._has_standings_table(table_data),
                }
            except Exception:
                result[match_id] = fallback

        return result

    def _save_html_snapshots(
        self, html_pages: dict[str, str], *, match_id: str, date_iso: str | None
    ) -> dict[str, str]:
        date_part = str(date_iso or "undated").strip() or "undated"
        root = resolve_processed_dir(self.config) / "headless_html" / date_part / (
            match_id or "unknown_match"
        )
        root.mkdir(parents=True, exist_ok=True)

        saved: dict[str, str] = {}
        for name, html in html_pages.items():
            path = root / f"{name}.html"
            path.write_text(str(html or ""), encoding="utf-8")
            saved[name] = str(path)
        return saved

    @staticmethod
    def _parse_date_text_to_iso(date_text: str | None) -> str | None:
        text = str(date_text or "").strip()
        if not text:
            return None

        match = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", text)
        if not match:
            return None

        day = int(match.group(1))
        month = int(match.group(2))
        year = int(match.group(3))
        if year < 100:
            year += 2000

        try:
            return datetime(year, month, day).strftime("%Y-%m-%d")
        except Exception:
            return None

    @staticmethod
    def to_serializable(result: MatchPipelineResult) -> dict:
        return {
            "match_id": result.match_id,
            "routes": result.routes,
            "html_paths": result.html_paths,
            "json_path": result.json_path,
            "payload": result.payload,
        }

    @staticmethod
    def to_json(result: MatchPipelineResult) -> str:
        return json.dumps(
            MatchPipeline.to_serializable(result),
            indent=2,
            ensure_ascii=False,
        )

    @staticmethod
    def _is_zero_zero(score_home: str, score_away: str) -> bool:
        return (
            str(score_home or "").strip() == "0"
            and str(score_away or "").strip() == "0"
        )

    @staticmethod
    def _empty_table_block() -> dict:
        return {
            "total_rows": 0,
            "promotions": 0,
            "relegations": 0,
            "home_team": {},
            "away_team": {},
            "all": [],
        }

    @staticmethod
    def _has_standings_table(table_data: dict | None) -> bool:
        if not isinstance(table_data, dict):
            return False
        return bool(table_data.get("all"))

    @staticmethod
    def _last_h2h_key(team_name: str) -> str:
        return f"last_h2h::{team_name}"

    @staticmethod
    def _last_standings_key(team_name: str) -> str:
        return f"last_standings::{team_name}"

    @staticmethod
    def _h2h_standings_key(match_id: str) -> str:
        return f"h2h_standings::{match_id}"

    @staticmethod
    def _route_url_for(match_url: str, attr_name: str) -> str:
        routes = build_match_routes(match_url)
        return str(getattr(routes, attr_name))
