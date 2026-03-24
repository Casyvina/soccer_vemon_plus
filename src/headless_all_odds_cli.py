from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.managers.config_manager import ConfigManager
from headless.odds_fetch import SeleniumOddsPageFetcher
from headless.pipeline.all_odds_pipeline import AllOddsPipeline
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect daily Flashscore odds snapshots into per-date JSON files."
    )
    parser.add_argument(
        "--day",
        action="append",
        type=int,
        default=[],
        help="Day offset from today. Repeat to fetch multiple days. Allowed: 0..5.",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "firefox", "edge"],
        help="Override the configured browser for this run.",
    )
    parser.add_argument(
        "--base-dir",
        help="Override the data root for this run. Defaults to a repo-local _headless_output folder.",
    )
    parser.add_argument(
        "--no-save-html",
        action="store_true",
        help="Skip saving the rendered odds HTML snapshot.",
    )
    parser.add_argument(
        "--no-save-json",
        action="store_true",
        help="Skip saving the merged all_odds JSON.",
    )
    parser.add_argument(
        "--print-json",
        action="store_true",
        help="Print the pipeline result JSON to stdout.",
    )
    return parser


def _load_day_offsets(args) -> list[int]:
    if not args.day:
        return [0]

    seen: set[int] = set()
    offsets: list[int] = []
    for value in args.day:
        offset = int(value)
        if offset < 0 or offset > 5:
            raise ValueError("Day offsets must be between 0 and 5.")
        if offset in seen:
            continue
        seen.add(offset)
        offsets.append(offset)
    return offsets


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

    try:
        day_offsets = _load_day_offsets(args)
    except ValueError as exc:
        parser.error(str(exc))

    load_env_from_assets()
    base_dir = _configure_output_root(args)
    config = ConfigManager()
    ensure_app_dirs(config)

    page_source_fetcher = SeleniumOddsPageFetcher(
        config=config,
        browser_name=args.browser,
    )
    pipeline = AllOddsPipeline(config=config, page_source_fetcher=page_source_fetcher)
    results = pipeline.run_for_days(
        day_offsets,
        save_html=(not args.no_save_html),
        save_json_payload=(not args.no_save_json),
    )

    print(f"Base dir: {base_dir}")
    for result in results:
        print(
            f"Day {result.day_offset}: {result.date_iso} "
            f"({result.selected_day_label}) -> {result.match_count} matches"
        )
        if result.json_path:
            print(f"All odds JSON: {result.json_path}")
        if result.html_path:
            print(f"HTML snapshot: {result.html_path}")
        print(
            "Merge stats: "
            f"added={result.merge_stats.get('added', 0)} "
            f"updated={result.merge_stats.get('updated', 0)} "
            f"unchanged={result.merge_stats.get('unchanged', 0)}"
        )

    if args.print_json:
        serializable = [AllOddsPipeline.to_serializable(item) for item in results]
        print(
            json.dumps(
                serializable if len(serializable) != 1 else serializable[0],
                indent=2,
                ensure_ascii=False,
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
