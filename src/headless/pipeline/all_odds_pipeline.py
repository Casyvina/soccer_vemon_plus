from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from headless.odds_fetch import OddsPageFetchResult, SeleniumOddsPageFetcher
from headless.parsers.match_summary import parse_match_summary
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
    list_halftime_score_candidates,
    load_json,
    merge_all_odds,
    save_json,
    summarize_score_progress,
    upsert_scores_in_payload,
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
    def __init__(self, config=None, page_source_fetcher=None, supabase_manager=None):
        self.config = config
        self.page_source_fetcher = page_source_fetcher or SeleniumOddsPageFetcher(
            config=config
        )
        self.supabase_manager = supabase_manager

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
                if self.supabase_manager:
                    enriched = {
                        "date": date_iso,
                        "counts": {
                            "total": len(payload.get("matches") or {}),
                            "added_last_merge": merge_stats.get("added", 0),
                            "updated_last_merge": merge_stats.get("updated", 0),
                        },
                        **payload,
                    }
                    self.supabase_manager.upsert_all_odds_snapshot(date_iso, enriched)
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

    def run_halftime_score_refresh(
        self,
        date_iso: str,
        *,
        limit: int = 0,
        batch_size: int = 5,
        persist: bool = True,
    ) -> dict:
        """
        For a given date, find past matches missing half-time scores, visit their
        summary pages in parallel-tab batches, parse scores, update the JSON, and
        optionally push to Supabase scores table.

        batch_size: how many match summary tabs to open in parallel per round.

        Returns a summary dict:
          {"date_iso", "candidates", "processed", "updated", "failed", "skipped"}
        """
        target_dir = resolve_all_odds_dir(self.config)
        json_path = target_dir / f"{date_iso}.json"
        if not json_path.exists():
            raise ValueError(f"All odds file not found: {json_path}")

        payload = load_json(json_path)
        candidates = list_halftime_score_candidates(
            payload, limit=limit, date_iso=date_iso, buffer_hours=3
        )

        processed = 0
        updated = 0
        failed = 0
        batch_size = max(1, int(batch_size or 5))

        fetch_summary = getattr(self.page_source_fetcher, "fetch_summary_pages", None)
        if not callable(fetch_summary):
            return {
                "date_iso": date_iso,
                "candidates": len(candidates),
                "processed": 0,
                "updated": 0,
                "failed": 0,
                "skipped": len(candidates),
                "error": "page_source_fetcher does not support fetch_summary_pages",
            }

        for batch_start in range(0, len(candidates), batch_size):
            batch = candidates[batch_start: batch_start + batch_size]
            items = [(c["match_id"], c["url"]) for c in batch]
            try:
                pages = fetch_summary(items)
            except Exception:
                failed += len(batch)
                processed += len(batch)
                continue

            for candidate in batch:
                match_id = candidate["match_id"]
                url = candidate["url"]
                processed += 1
                try:
                    html = pages.get(match_id) or ""
                    summary = parse_match_summary(html)

                    scores = {
                        "1h_home": summary.get("1h_home"),
                        "1h_away": summary.get("1h_away"),
                        "2h_home": summary.get("2h_home"),
                        "2h_away": summary.get("2h_away"),
                        "ft_home": summary.get("ft_home"),
                        "ft_away": summary.get("ft_away"),
                    }
                    changed = upsert_scores_in_payload(payload, match_id, scores)
                    if changed:
                        updated += 1

                    if self.supabase_manager:
                        match_item = (payload.get("matches") or {}).get(match_id) or {}
                        full_scores = (match_item.get("scores") or {})
                        self.supabase_manager.upsert_score(
                            match_id=match_id,
                            date_iso=date_iso,
                            url=url,
                            home=str(candidate.get("home") or ""),
                            away=str(candidate.get("away") or ""),
                            scores=full_scores,
                        )
                except Exception:
                    failed += 1

        if persist and (updated or failed == 0):
            save_json(json_path, payload)

        return {
            "date_iso": date_iso,
            "candidates": len(candidates),
            "processed": processed,
            "updated": updated,
            "failed": failed,
            "skipped": len(candidates) - processed,
        }

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
