from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from headless.odds_fetch import OddsPageFetchResult, SeleniumOddsPageFetcher
from headless.parsers.all_odds import (
    build_all_odds_snapshot,
    infer_selected_date_iso_from_label,
)
from utils.all_odds_score_state import (
    get_day_state,
    load_all_odds_score_state,
    mark_day_state,
    save_all_odds_score_state,
)
from utils.all_odds_store import (
    load_json,
    merge_all_odds,
    save_json,
    summarize_score_progress,
)
from utils.paths import resolve_all_odds_dir, resolve_processed_dir


@dataclass
class AllOddsPipelineResult:
    day_offset: int
    date_iso: str
    selected_day_label: str
    page_url: str
    html_path: str
    json_path: str
    payload: dict
    match_count: int
    merge_stats: dict
    score_state_path: str
    score_summary: dict


class AllOddsPipeline:
    def __init__(self, config=None, page_source_fetcher=None):
        self.config = config
        self.page_source_fetcher = page_source_fetcher or SeleniumOddsPageFetcher(
            config=config
        )

    def run_for_day(
        self,
        day_offset: int,
        *,
        save_html: bool = True,
        save_json_payload: bool = True,
    ) -> AllOddsPipelineResult:
        return self.run_for_days(
            [day_offset],
            save_html=save_html,
            save_json_payload=save_json_payload,
        )[0]

    def run_for_days(
        self,
        day_offsets: list[int],
        *,
        save_html: bool = True,
        save_json_payload: bool = True,
    ) -> list[AllOddsPipelineResult]:
        fetched = self.page_source_fetcher.fetch_day_pages(day_offsets)
        results: list[AllOddsPipelineResult] = []

        for day_offset in self._normalize_day_offsets(day_offsets):
            page = fetched[int(day_offset)]
            date_iso = self._resolve_date_iso(page)
            snapshot = build_all_odds_snapshot(
                page.html,
                page_url=page.page_url,
                day_offset=day_offset,
                date_iso=date_iso,
            )
            match_count = len(snapshot.get("matches") or {})
            if not match_count:
                raise ValueError(f"No odds matches parsed for day offset {day_offset}.")

            html_path = self._save_html_snapshot(page, date_iso) if save_html else ""

            payload = snapshot
            json_path = ""
            merge_stats = {"added": 0, "updated": 0, "unchanged": 0}
            if save_json_payload:
                json_path, payload, merge_stats = self._merge_and_save_snapshot(
                    snapshot=snapshot,
                    page=page,
                    date_iso=date_iso,
                )
                score_state_path, score_summary = self._update_score_state(
                    payload=payload,
                    date_iso=date_iso,
                )
            else:
                score_state_path = ""
                score_summary = {}

            results.append(
                AllOddsPipelineResult(
                    day_offset=int(day_offset),
                    date_iso=date_iso,
                    selected_day_label=str(page.selected_day_label or "").strip(),
                    page_url=str(page.page_url or "").strip(),
                    html_path=str(html_path or ""),
                    json_path=str(json_path or ""),
                    payload=payload,
                    match_count=match_count,
                    merge_stats=merge_stats,
                    score_state_path=str(score_state_path or ""),
                    score_summary=score_summary,
                )
            )

        return results

    def _merge_and_save_snapshot(
        self,
        *,
        snapshot: dict,
        page: OddsPageFetchResult,
        date_iso: str,
    ) -> tuple[str, dict, dict]:
        target_dir = resolve_all_odds_dir(self.config)
        target_dir.mkdir(parents=True, exist_ok=True)
        json_path = target_dir / f"{date_iso}.json"

        existing = load_json(json_path)
        merged, stats = merge_all_odds(existing, snapshot)
        merged["date"] = date_iso
        merged["day_offset"] = int(page.day_offset)
        merged["selected_day_label"] = str(page.selected_day_label or "").strip()
        merged["page_url"] = str(page.page_url or "").strip()
        merged["fetched_at"] = datetime.now().isoformat(timespec="seconds")

        save_json(json_path, merged)

        return (
            str(json_path),
            merged,
            {
                "added": int(stats.added),
                "updated": int(stats.updated),
                "unchanged": int(stats.unchanged),
            },
        )

    def _update_score_state(
        self,
        *,
        payload: dict,
        date_iso: str,
    ) -> tuple[str, dict]:
        path = resolve_processed_dir(self.config) / "all_odds_scores_state.json"
        state = load_all_odds_score_state(path)
        previous = get_day_state(state, date_iso) or {}
        previous_match_count = previous.get("match_count")
        summary = summarize_score_progress(payload)

        mark_day_state(
            state,
            date_iso,
            match_count=int(summary.get("match_count", 0)),
            scored_count=int(summary.get("scored_count", 0)),
            pending_eligible_count=int(summary.get("pending_eligible_count", 0)),
            future_blocked_count=int(summary.get("future_blocked_count", 0)),
            status=str(summary.get("status") or ""),
            count_changed=(
                previous_match_count is None
                or int(previous_match_count) != int(summary.get("match_count", 0))
            ),
            previous_match_count=(
                int(previous_match_count)
                if previous_match_count is not None
                else None
            ),
        )
        save_all_odds_score_state(path, state)
        return str(path), summary

    def _save_html_snapshot(self, page: OddsPageFetchResult, date_iso: str) -> str:
        root = resolve_processed_dir(self.config) / "headless_all_odds_html" / date_iso
        root.mkdir(parents=True, exist_ok=True)
        path = root / "odds.html"
        path.write_text(str(page.html or ""), encoding="utf-8")
        return str(path)

    @staticmethod
    def _resolve_date_iso(page: OddsPageFetchResult) -> str:
        date_iso = infer_selected_date_iso_from_label(
            page.selected_day_label,
        )
        if date_iso and date_iso != "unknown":
            return date_iso

        return (datetime.now() + timedelta(days=int(page.day_offset))).strftime(
            "%Y-%m-%d"
        )

    @staticmethod
    def _normalize_day_offsets(day_offsets: list[int]) -> list[int]:
        if not day_offsets:
            return [0]
        seen: set[int] = set()
        normalized: list[int] = []
        for value in day_offsets:
            offset = int(value)
            if offset in seen:
                continue
            seen.add(offset)
            normalized.append(offset)
        return normalized

    @staticmethod
    def to_serializable(result: AllOddsPipelineResult) -> dict:
        return {
            "day_offset": result.day_offset,
            "date_iso": result.date_iso,
            "selected_day_label": result.selected_day_label,
            "page_url": result.page_url,
            "html_path": result.html_path,
            "json_path": result.json_path,
            "payload": result.payload,
            "match_count": result.match_count,
            "merge_stats": result.merge_stats,
            "score_state_path": result.score_state_path,
            "score_summary": result.score_summary,
        }

    @staticmethod
    def to_json(result: AllOddsPipelineResult) -> str:
        return json.dumps(
            AllOddsPipeline.to_serializable(result),
            indent=2,
            ensure_ascii=False,
        )
