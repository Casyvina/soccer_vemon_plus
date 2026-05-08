from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.managers.config_manager import ConfigManager
from core.managers.supabase_manager import SupabaseManager
from headless.odds_fetch import SeleniumOddsPageFetcher
from headless.pipeline.all_odds_pipeline import AllOddsPipeline
from utils.all_odds_score_state import (
    list_recheck_candidates,
    load_all_odds_score_state,
)
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs, resolve_processed_dir


MIN_DAY_OFFSET = -7
MAX_DAY_OFFSET = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect daily Flashscore odds snapshots into per-date JSON files."
    )
    parser.add_argument(
        "--day",
        action="append",
        type=int,
        default=[],
        help="Day offset from today. Repeat to fetch multiple days. Allowed: -7..5.",
    )
    parser.add_argument(
        "--recheck-open-days",
        action="store_true",
        help="Load day offsets from all_odds_scores_state.json for dates still marked pending or incomplete.",
    )
    parser.add_argument(
        "--recheck-limit",
        type=int,
        default=0,
        help="Limit the number of score-state recheck dates to load. Use 0 for all eligible dates.",
    )
    parser.add_argument(
        "--include-failed-days",
        action="store_true",
        help="When using --recheck-open-days, also retry dates recorded under failed_dates in the score-state file.",
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


def _load_day_offsets(args, config) -> tuple[list[int], list[dict[str, object]], Path]:
    seen: set[int] = set()
    offsets: list[int] = []
    for value in args.day or []:
        offset = int(value)
        if offset < MIN_DAY_OFFSET or offset > MAX_DAY_OFFSET:
            raise ValueError(
                f"Day offsets must be between {MIN_DAY_OFFSET} and {MAX_DAY_OFFSET}."
            )
        if offset in seen:
            continue
        seen.add(offset)
        offsets.append(offset)

    state_path = resolve_processed_dir(config) / "all_odds_scores_state.json"
    recheck_candidates: list[dict[str, object]] = []
    if args.recheck_open_days:
        if state_path.exists():
            state = load_all_odds_score_state(state_path)
            recheck_candidates = list_recheck_candidates(
                state,
                include_failed=bool(args.include_failed_days),
                min_day_offset=MIN_DAY_OFFSET,
                max_day_offset=MAX_DAY_OFFSET,
                limit=max(0, int(args.recheck_limit)),
            )
            for item in recheck_candidates:
                offset = int(item.get("day_offset") or 0)
                if offset in seen:
                    continue
                seen.add(offset)
                offsets.append(offset)
        elif not offsets:
            return [], [], state_path

    if not offsets and not args.recheck_open_days:
        return [0], [], state_path

    return offsets, recheck_candidates, state_path


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
        day_offsets, recheck_candidates, score_state_path = _load_day_offsets(args, config)
    except ValueError as exc:
        parser.error(str(exc))

    if not day_offsets:
        if args.recheck_open_days:
            print(f"Base dir: {base_dir}")
            print(f"Score state source: {score_state_path}")
            print("No recheck candidates found in all_odds_scores_state.json")
            return 0
        parser.error("Provide at least one valid day offset.")

    page_source_fetcher = SeleniumOddsPageFetcher(
        config=config,
        browser_name=args.browser,
    )
    supabase_manager = SupabaseManager(config=config)
    pipeline = AllOddsPipeline(
        config=config,
        page_source_fetcher=page_source_fetcher,
        supabase_manager=supabase_manager,
    )
    results = pipeline.run_for_days(
        day_offsets,
        save_html=(not args.no_save_html),
        save_json_payload=(not args.no_save_json),
    )

    print(f"Base dir: {base_dir}")
    if args.recheck_open_days:
        print(f"Score state source: {score_state_path}")
        print(
            f"Open-day recheck selection: {len(recheck_candidates)} date(s) "
            f"within offsets {MIN_DAY_OFFSET}..{MAX_DAY_OFFSET}"
        )
        for item in recheck_candidates:
            print(
                "  "
                f"{item.get('date_iso')} -> day {item.get('day_offset')} "
                f"[{item.get('status')}]"
            )
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
        if result.score_summary:
            print(
                "Score state: "
                f"status={result.score_summary.get('status', '')} "
                f"scored={result.score_summary.get('scored_count', 0)}/"
                f"{result.score_summary.get('match_count', 0)} "
                f"pending={result.score_summary.get('pending_eligible_count', 0)} "
                f"future={result.score_summary.get('future_blocked_count', 0)}"
            )
        if result.score_state_path:
            print(f"Score state JSON: {result.score_state_path}")

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
