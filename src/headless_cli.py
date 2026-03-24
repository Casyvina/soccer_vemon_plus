from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from core.managers.config_manager import ConfigManager
from headless.pipeline.match_pipeline import MatchPipeline
from headless.selenium_fetch import SeleniumPageSourceFetcher
from utils.all_odds_store import (
    extract_match_id,
    load_json,
    mark_details_fetched_in_payload,
    save_json,
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
        "--limit",
        type=int,
        help="Limit the final number of match URLs processed after source loading and dedupe.",
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


def _load_all_odds_urls(args, config) -> tuple[list[str], Path | None, dict | None]:
    path = _resolve_all_odds_path(args, config)
    if path is None:
        return [], None, None

    if not path.exists():
        raise ValueError(f"All odds source file not found: {path}")

    payload = load_json(path)
    matches = payload.get("matches") or {}
    urls: list[str] = []
    for _, item in matches.items():
        if not isinstance(item, dict):
            continue
        if not args.include_fetched and bool(item.get("details_fetched")):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        urls.append(url)

    return urls, path, payload


def _dedupe_urls(urls: list[str], limit: int | None = None) -> list[str]:
    seen: set[str] = set()
    unique_urls: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique_urls.append(url)

    if limit is not None and int(limit) > 0:
        return unique_urls[: int(limit)]
    return unique_urls


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
        all_odds_urls, all_odds_path, all_odds_payload = _load_all_odds_urls(
            args, config
        )
    except ValueError as exc:
        parser.error(str(exc))

    urls = _dedupe_urls(direct_urls + all_odds_urls, limit=args.limit)
    if not urls:
        parser.error(
            "Provide at least one --url/--urls-file or one all_odds source."
        )

    page_source_fetcher = (
        SeleniumPageSourceFetcher(config=config, browser_name=args.browser)
        if args.rendered
        else None
    )
    context = page_source_fetcher if page_source_fetcher is not None else nullcontext()
    results = []
    failures = 0
    all_odds_dirty = False

    with context:
        pipeline = MatchPipeline(config=config, page_source_fetcher=page_source_fetcher)

        for url in urls:
            try:
                result = pipeline.run_from_url(
                    url,
                    save_html=(not args.no_save_html),
                    save_json=(not args.no_save_json),
                )
                results.append(result)
                if (
                    all_odds_payload is not None
                    and not args.no_save_json
                    and mark_details_fetched_in_payload(
                        all_odds_payload,
                        result.match_id or extract_match_id(url),
                        True,
                    )
                ):
                    all_odds_dirty = True
            except Exception as exc:
                failures += 1
                print(f"Failed: {url}")
                print(f"  Error: {exc}")

    if all_odds_dirty and all_odds_path is not None:
        save_json(all_odds_path, all_odds_payload)

    print(f"Base dir: {base_dir}")
    if all_odds_path is not None:
        print(f"All odds source: {all_odds_path}")
    if page_source_fetcher is not None and len(urls) > 1:
        print(f"Rendered batch mode: reusing one browser session for {len(urls)} matches")
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
