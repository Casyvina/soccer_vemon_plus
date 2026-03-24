from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from headless.http import HeadlessHttpClient
from headless.parsers.h2h import parse_h2h_sections
from headless.parsers.match import parse_match_page
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
        html_pages = self._fetch_route_pages(routes)

        match_block = parse_match_page(html_pages["match"])
        breadcrumb = match_block.get("breadcrumb") or {}
        infobox = str(match_block.get("infobox") or "")
        match_details = match_block.get("match_details") or {}

        home_team = str(match_details.get("home_team") or "")
        away_team = str(match_details.get("away_team") or "")

        h2h_sections = parse_h2h_sections(
            html_pages["h2h_overall"], source_url=routes.h2h_overall_url
        )
        standings_overall = parse_standings_page(
            html_pages["standings_overall"], home_team, away_team
        )
        standings_home = parse_standings_page(
            html_pages["standings_home"], home_team, away_team
        )
        standings_away = parse_standings_page(
            html_pages["standings_away"], home_team, away_team
        )

        supplemental_pages = self._fetch_named_pages(
            self._build_supplemental_requests(h2h_sections)
        )

        payload = {
            "url": routes.match_url,
            "breadcrumb": breadcrumb,
            "infobox": infobox,
            "match_details": match_details,
            "odds": {},
            "h2h": h2h_sections,
            "table": standings_overall,
            "table_home_only": standings_home,
            "table_away_only": standings_away,
            "last_matches": self._build_last_matches(h2h_sections, supplemental_pages),
            "h2h_standings": self._build_h2h_standings(
                h2h_sections, supplemental_pages
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
                ("standings_home", routes.standings_home_url),
                ("standings_away", routes.standings_away_url),
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

    def _build_supplemental_requests(
        self, h2h_sections: list[dict]
    ) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []

        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            title_upper = title.upper()
            matches = [
                item for item in (section.get("matches") or []) if isinstance(item, dict)
            ]
            if title_upper.startswith("LAST MATCHES:") and matches:
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
                section
                for section in h2h_sections
                if "HEAD-TO-HEAD" in str(section.get("section_title") or "").upper()
            ),
            None,
        )
        if not h2h_section:
            return items

        matches = [
            item for item in (h2h_section.get("matches") or []) if isinstance(item, dict)
        ][:5]
        for match_item in matches:
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

    def _build_last_matches(self, h2h_sections: list[dict], pages: dict[str, str]) -> dict:
        result: dict[str, dict] = {}

        for section in h2h_sections:
            title = str(section.get("section_title") or "").strip()
            title_upper = title.upper()
            if not title_upper.startswith("LAST MATCHES:"):
                continue

            team_name = title.split(":", 1)[-1].strip()
            matches = [
                item for item in (section.get("matches") or []) if isinstance(item, dict)
            ]
            if not team_name or not matches:
                continue

            last_match = matches[0]
            result[team_name] = self._build_last_match_payload(
                team_name,
                last_match,
                pages,
            )

        return result

    def _build_last_match_payload(
        self,
        team_name: str,
        match_item: dict,
        pages: dict[str, str],
    ) -> dict:
        match_url = str(match_item.get("url") or "").strip()
        home_team = str(match_item.get("home") or "").strip()
        away_team = str(match_item.get("away") or "").strip()

        fallback = {
            "match_url": match_url,
            "h2h": [],
            "table": self._empty_table_block(),
            "odds_data": {},
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

            return {
                "match_url": match_url,
                "h2h": h2h_data,
                "table": table_data if self._has_standings_table(table_data) else self._empty_table_block(),
                "odds_data": {},
                "has_table": self._has_standings_table(table_data),
                "has_odds": False,
            }
        except Exception:
            return fallback

    def _build_h2h_standings(
        self,
        h2h_sections: list[dict],
        pages: dict[str, str],
    ) -> dict:
        result: dict[str, dict] = {}
        h2h_section = next(
            (
                section
                for section in h2h_sections
                if "HEAD-TO-HEAD" in str(section.get("section_title") or "").upper()
            ),
            None,
        )
        if not h2h_section:
            return result

        matches = [
            item for item in (h2h_section.get("matches") or []) if isinstance(item, dict)
        ][:5]

        for match_item in matches:
            match_url = str(match_item.get("url") or "").strip()
            match_id = extract_match_id(match_url)
            if not match_id:
                continue

            home_team = str(match_item.get("home") or "").strip()
            away_team = str(match_item.get("away") or "").strip()
            fallback = {
                "match_url": match_url,
                "total_rows": 0,
                "promotions": 0,
                "relegations": 0,
                "home_team": {},
                "away_team": {},
                "odds_data": {},
                "has_table": False,
            }

            try:
                table_data = parse_standings_page(
                    pages.get(self._h2h_standings_key(match_id), ""),
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
