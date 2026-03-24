from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.managers.config_manager import ConfigManager
from headless.league_fetch import SeleniumLeaguePageFetcher
from headless.pipeline.league_pipeline import LeaguePipeline
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Local hybrid league results/fixtures scraper."
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="League URL. Results, fixtures, or base competition URL all work.",
    )
    parser.add_argument(
        "--urls-file",
        help="Text file with one league URL per line.",
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
        help="Skip saving match_index.json.",
    )
    parser.add_argument(
        "--export-sheets",
        action="store_true",
        help="Also export Excel and CSV after writing match_index.json.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the pipeline result JSON to stdout.",
    )
    return parser


def _load_urls(args) -> list[str]:
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

    seen: set[str] = set()
    unique: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        unique.append(url)
    return unique


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
    urls = _load_urls(args)
    if not urls:
        parser.error("Provide at least one --url or --urls-file.")

    load_env_from_assets()
    base_dir = _configure_output_root(args)
    config = ConfigManager()
    ensure_app_dirs(config)

    fetcher = SeleniumLeaguePageFetcher(config=config)
    pipeline = LeaguePipeline(config=config, page_source_fetcher=fetcher)

    results = []
    failures = 0
    for url in urls:
        try:
            result = pipeline.run_from_url(
                url,
                save_html=(not args.no_save_html),
                save_json=(not args.no_save_json),
                export_sheets=bool(args.export_sheets),
            )
            results.append(result)
        except Exception as exc:
            failures += 1
            print(f"Failed: {url}")
            print(f"  Error: {exc}")

    print(f"Base dir: {base_dir}")
    for result in results:
        print(f"Results URL: {result.results_url}")
        print(f"Fixtures URL: {result.fixtures_url}")
        print(
            "Counts: "
            f"results={result.payload.get('results_count', 0)} "
            f"fixtures={result.payload.get('fixtures_count', 0)}"
        )
        if result.json_path:
            print(f"Match index: {result.json_path}")
        if result.excel_path:
            print(f"Excel: {result.excel_path}")
        if result.csv_path:
            print(f"CSV: {result.csv_path}")
        if result.html_paths:
            print("HTML snapshots:")
            for key, value in result.html_paths.items():
                print(f"  {key}: {value}")

    if len(results) > 1 or failures:
        print(f"Processed {len(results)} league(s); failures: {failures}")

    if args.print_json:
        serializable = [LeaguePipeline.to_serializable(item) for item in results]
        print(
            json.dumps(
                serializable if len(serializable) != 1 else serializable[0],
                indent=2,
                ensure_ascii=False,
            )
        )

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
