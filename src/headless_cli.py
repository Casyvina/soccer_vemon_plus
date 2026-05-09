from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.managers.config_manager import ConfigManager
from core.managers.supabase_manager import SupabaseManager
from headless.pipeline.match_pipeline import MatchPipeline
from headless.selenium_fetch import SeleniumPageSourceFetcher
from utils.market_payload_builder import build_market_match, _format_market_day_label
from utils.all_odds_store import (
    begin_details_batch_in_payload,
    finish_details_batch_in_payload,
    extract_match_id,
    list_detail_candidates,
    load_json,
    mark_details_failed_in_payload,
    mark_details_fetched_in_payload,
    save_json,
    start_details_attempt_in_payload,
    update_details_batch_progress_in_payload,
)
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs, resolve_all_odds_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local single-match headless scraper bootstrap."
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Flashscore match or subpage URL. Repeat for multiple matches.",
    )
    parser.add_argument(
        "--urls-file",
        help="Text file with one Flashscore match URL per line.",
    )
    parser.add_argument(
        "--all-odds-date",
        help="Load match URLs from data/raw/all_odds/YYYY-MM-DD.json under the active base dir.",
    )
    parser.add_argument(
        "--all-odds-day",
        type=int,
        help="Load match URLs from the saved all_odds file for day offset 0..5.",
    )
    parser.add_argument(
        "--all-odds-file",
        help="Load match URLs from an explicit all_odds json file path.",
    )
    parser.add_argument(
        "--include-fetched",
        action="store_true",
        help="Include all_odds entries already marked details_fetched=true.",
    )
    parser.add_argument(
        "--only-failed",
        action="store_true",
        help="When using an all_odds source, only retry entries whose last status is failed.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="When using an all_odds source, skip unfetched entries that already reached this attempt count. Use 0 for unlimited.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the final number of match URLs processed after source loading and dedupe.",
    )
    parser.add_argument(
        "--cache-size",
        type=int,
        default=60,
        help="Maximum number of rendered/raw page HTML responses to keep in the cross-match cache. Use 0 to disable caching.",
    )
    parser.add_argument(
        "--clear-cache-per-match",
        action="store_true",
        help="Reset the page cache after every match instead of reusing it across the batch.",
    )
    parser.add_argument(
        "--delay-between-matches",
        type=float,
        default=0.0,
        help="Sleep this many seconds after each successful match before moving to the next one.",
    )
    parser.add_argument(
        "--delay-after-failure",
        type=float,
        default=0.0,
        help="Sleep this many seconds after a failed match before continuing.",
    )
    parser.add_argument(
        "--base-dir",
        help="Override the data root for this run. Defaults to a repo-local _headless_output folder.",
    )
    parser.add_argument(
        "--no-save-html",
        action="store_true",
        help="Skip saving fetched HTML snapshots.",
    )
    parser.add_argument(
        "--no-save-json",
        action="store_true",
        help="Skip saving raw match JSON.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the pipeline result JSON to stdout.",
    )
    parser.add_argument(
        "--rendered",
        action="store_true",
        help="Use a short-lived headless Selenium browser to capture rendered page source.",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "firefox", "edge"],
        help="Override the configured browser for rendered mode.",
    )
    parser.add_argument(
        "--db-batch-size",
        type=int,
        default=50,
        help="Number of match results to accumulate before flushing to Supabase. Use 1 to push after every match, 0 to push only at the end.",
    )
    parser.add_argument(
        "--restart-every",
        type=int,
        default=5,
        help="Restart the browser session every N matches to prevent memory bloat. Use 0 to disable.",
    )
    return parser


def _load_urls_from_direct_sources(args) -> list[str]:
    urls: list[str] = []

    for value in args.url or []:
        text = str(value or "").strip()
        if text:
            urls.append(text)

    if args.urls_file:
        path = Path(str(args.urls_file)).expanduser()
        for line in path.read_text(encoding="utf-8").splitlines():
            text = str(line or "").strip()
            if not text or text.startswith("#"):
                continue
            urls.append(text)

    return urls


def _resolve_all_odds_path(args, config) -> Path | None:
    provided = [
        bool(args.all_odds_date),
        args.all_odds_day is not None,
        bool(args.all_odds_file),
    ]
    if sum(1 for item in provided if item) > 1:
        raise ValueError(
            "Use only one of --all-odds-date, --all-odds-day, or --all-odds-file."
        )

    if args.all_odds_file:
        return Path(str(args.all_odds_file)).expanduser()

    if args.all_odds_date:
        date_text = str(args.all_odds_date).strip()
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                "--all-odds-date must use YYYY-MM-DD format."
            ) from exc
        return resolve_all_odds_dir(config) / f"{date_text}.json"

    if args.all_odds_day is None:
        return None

    day_offset = int(args.all_odds_day)
    if day_offset < 0 or day_offset > 5:
        raise ValueError("--all-odds-day must be between 0 and 5.")

    date_iso = (datetime.now() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
    return resolve_all_odds_dir(config) / f"{date_iso}.json"


def _load_all_odds_candidates(
    args, config
) -> tuple[list[dict[str, str]], Path | None, dict | None]:
    path = _resolve_all_odds_path(args, config)
    if path is None:
        return [], None, None

    if not path.exists():
        raise ValueError(f"All odds source file not found: {path}")

    payload = load_json(path)
    candidates = list_detail_candidates(
        payload,
        include_fetched=args.include_fetched,
        only_failed=args.only_failed,
        max_attempts=max(0, int(args.max_attempts)),
    )
    return candidates, path, payload


def _build_work_items(
    direct_urls: list[str],
    all_odds_candidates: list[dict[str, str]],
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = [
        {
            "url": str(url).strip(),
            "all_odds_match_id": "",
        }
        for url in direct_urls
        if str(url).strip()
    ]
    items.extend(
        {
            "url": str(item.get("url") or "").strip(),
            "all_odds_match_id": str(item.get("match_id") or "").strip(),
        }
        for item in all_odds_candidates
        if str(item.get("url") or "").strip()
    )

    deduped: list[dict[str, str]] = []
    by_url: dict[str, dict[str, str]] = {}
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url:
            continue

        existing = by_url.get(url)
        if existing is None:
            normalized = {
                "url": url,
                "all_odds_match_id": str(item.get("all_odds_match_id") or "").strip(),
            }
            by_url[url] = normalized
            deduped.append(normalized)
            continue

        if not existing.get("all_odds_match_id") and item.get("all_odds_match_id"):
            existing["all_odds_match_id"] = str(item.get("all_odds_match_id") or "").strip()

    if limit is not None and int(limit) > 0:
        return deduped[: int(limit)]
    return deduped


def _configure_output_root(args) -> Path:
    base_dir = (
        Path(str(args.base_dir)).expanduser()
        if args.base_dir
        else (Path.cwd() / "_headless_output")
    ).resolve()
    os.environ["SOCCER_PLACE_DATA_DIR"] = str(base_dir)
    os.environ["SOCCER_SCENT_RAW_DIR"] = str(base_dir / "data" / "raw")
    return base_dir


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_env_from_assets()
    base_dir = _configure_output_root(args)
    config = ConfigManager()
    ensure_app_dirs(config)

    try:
        direct_urls = _load_urls_from_direct_sources(args)
        all_odds_candidates, all_odds_path, all_odds_payload = _load_all_odds_candidates(
            args, config
        )
    except ValueError as exc:
        parser.error(str(exc))

    work_items = _build_work_items(
        direct_urls,
        all_odds_candidates,
        limit=args.limit,
    )
    if not work_items:
        parser.error(
            "Provide at least one --url/--urls-file or one all_odds source."
        )
    urls = [item["url"] for item in work_items]

    supabase_manager = SupabaseManager(config=config)
    page_source_fetcher = (
        SeleniumPageSourceFetcher(config=config, browser_name=args.browser)
        if args.rendered
        else None
    )
    context = page_source_fetcher if page_source_fetcher is not None else nullcontext()
    db_batch_size = max(0, int(args.db_batch_size))
    _date_iso_for_batch = all_odds_path.stem if all_odds_path else ""
    market_batch: list[dict] = []

    def _flush_market_batch() -> None:
        if not market_batch or not _date_iso_for_batch:
            return
        try:
            supabase_manager.upsert_market_day({
                "id": _date_iso_for_batch,
                "label": _format_market_day_label(_date_iso_for_batch),
                "matches": list(market_batch),
            })
            print(f"DB flush: {len(market_batch)} match(es) pushed to Supabase")
        except Exception:
            pass
        market_batch.clear()

    results = []
    failures = 0
    track_all_odds = all_odds_payload is not None and not args.no_save_json
    tracked_items = [item for item in work_items if item.get("all_odds_match_id")]
    tracked_total = len(tracked_items)
    tracked_processed = 0
    tracked_success = 0
    tracked_failure = 0
    tracked_dirty = False
    started_all_odds_batch = False

    if track_all_odds and all_odds_path is not None and tracked_total >= 0:
        begin_details_batch_in_payload(
            all_odds_payload,
            source=str(all_odds_path),
            planned_count=tracked_total,
            only_failed=args.only_failed,
            include_fetched=args.include_fetched,
            max_attempts=max(0, int(args.max_attempts)),
        )
        save_json(all_odds_path, all_odds_payload)
        started_all_odds_batch = True

    with context:
        pipeline = MatchPipeline(
            config=config,
            page_source_fetcher=page_source_fetcher,
            cache_enabled=(int(args.cache_size) > 0),
            max_cache_entries=max(0, int(args.cache_size)),
            reset_cache_per_match=args.clear_cache_per_match,
        )

        for index, item in enumerate(work_items, start=1):
            url = str(item.get("url") or "").strip()
            tracked_match_id = str(item.get("all_odds_match_id") or "").strip()
            is_last_item = index >= len(work_items)
            print(f"Fetching [{index}/{len(work_items)}]: {url}")

            if track_all_odds and tracked_match_id and all_odds_path is not None:
                start_details_attempt_in_payload(all_odds_payload, tracked_match_id)
                update_details_batch_progress_in_payload(
                    all_odds_payload,
                    processed_count=tracked_processed,
                    success_count=tracked_success,
                    failure_count=tracked_failure,
                    remaining_count=max(0, tracked_total - tracked_processed),
                    current_match_id=tracked_match_id,
                )
                save_json(all_odds_path, all_odds_payload)

            try:
                result = pipeline.run_from_url(
                    url,
                    save_html=(not args.no_save_html),
                    save_json=(not args.no_save_json),
                )
                results.append(result)
                if _date_iso_for_batch:
                    try:
                        market_match = build_market_match(
                            result.payload, _date_iso_for_batch, result.match_id
                        )
                        market_batch.append(market_match)
                        if db_batch_size > 0 and len(market_batch) >= db_batch_size:
                            _flush_market_batch()
                    except Exception:
                        pass
                if track_all_odds and tracked_match_id and all_odds_path is not None:
                    resolved_match_id = result.match_id or tracked_match_id or extract_match_id(url)
                    mark_details_fetched_in_payload(
                        all_odds_payload,
                        resolved_match_id,
                        True,
                    )
                    tracked_processed += 1
                    tracked_success += 1
                    tracked_dirty = True
                    update_details_batch_progress_in_payload(
                        all_odds_payload,
                        processed_count=tracked_processed,
                        success_count=tracked_success,
                        failure_count=tracked_failure,
                        remaining_count=max(0, tracked_total - tracked_processed),
                        current_match_id="",
                        last_completed_match_id=resolved_match_id,
                    )
                    save_json(all_odds_path, all_odds_payload)

                delay = max(0.0, float(args.delay_between_matches))
                if delay > 0 and not is_last_item:
                    time.sleep(delay)
            except Exception as exc:
                failures += 1
                print(f"Failed: {url}")
                print(f"  Error: {exc}")
                if track_all_odds and tracked_match_id and all_odds_path is not None:
                    mark_details_failed_in_payload(
                        all_odds_payload,
                        tracked_match_id,
                        str(exc),
                    )
                    tracked_processed += 1
                    tracked_failure += 1
                    tracked_dirty = True
                    update_details_batch_progress_in_payload(
                        all_odds_payload,
                        processed_count=tracked_processed,
                        success_count=tracked_success,
                        failure_count=tracked_failure,
                        remaining_count=max(0, tracked_total - tracked_processed),
                        current_match_id="",
                        last_completed_match_id=tracked_match_id,
                    )
                    save_json(all_odds_path, all_odds_payload)

                delay = max(0.0, float(args.delay_after_failure))
                if delay > 0 and not is_last_item:
                    time.sleep(delay)

            restart_every = max(0, int(args.restart_every))
            if (
                page_source_fetcher is not None
                and restart_every > 0
                and index % restart_every == 0
                and not is_last_item
            ):
                print(f"  Restarting browser after {restart_every} matches...")
                page_source_fetcher.close()
                page_source_fetcher.open()
                pipeline.reset_cache()

    if track_all_odds and started_all_odds_batch and all_odds_path is not None:
        finish_details_batch_in_payload(
            all_odds_payload,
            processed_count=tracked_processed,
            success_count=tracked_success,
            failure_count=tracked_failure,
            remaining_count=max(0, tracked_total - tracked_processed),
        )
        save_json(all_odds_path, all_odds_payload)

    _flush_market_batch()  # push any remaining matches

    print(f"Base dir: {base_dir}")
    if all_odds_path is not None:
        print(f"All odds source: {all_odds_path}")
    if all_odds_path is not None:
        print(
            "All odds selection: "
            f"{len(all_odds_candidates)} candidate(s), "
            f"max_attempts={max(0, int(args.max_attempts))}, "
            f"only_failed={bool(args.only_failed)}"
        )
    if page_source_fetcher is not None and len(urls) > 1:
        print(f"Rendered batch mode: reusing one browser session for {len(urls)} matches")
    stats = pipeline.cache_stats()
    print(
        "Page cache: "
        f"enabled={bool(stats['enabled'])} "
        f"entries={stats['entries']}/{stats['max_entries']} "
        f"hits={stats['hits']} misses={stats['misses']} evictions={stats['evictions']}"
    )
    for result in results:
        print(f"Match ID: {result.match_id}")
        if result.json_path:
            print(f"Raw JSON: {result.json_path}")
        if result.html_paths:
            print("HTML snapshots:")
            for key, value in result.html_paths.items():
                print(f"  {key}: {value}")

    if len(results) > 1 or failures:
        print(
            f"Processed {len(results)} match(es); failures: {failures}"
        )

    if args.print_json:
        serializable = [MatchPipeline.to_serializable(item) for item in results]
        print(json.dumps(serializable if len(serializable) != 1 else serializable[0], indent=2, ensure_ascii=False))

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
