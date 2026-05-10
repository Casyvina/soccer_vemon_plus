"""
CLI to fetch and store half-time + full-time scores for past matches.

Usage:
    python src/headless_score_cli.py --date 2026-05-08 --limit 10 --batch-size 5
    python src/headless_score_cli.py --date 2026-05-08               # all matches
    python src/headless_score_cli.py --date 2026-05-08 --dry-run     # show candidates only
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from core.managers.config_manager import ConfigManager
from core.managers.supabase_manager import SupabaseManager
from headless.pipeline.all_odds_pipeline import AllOddsPipeline
from headless.selenium_fetch import SeleniumPageSourceFetcher
from utils.all_odds_store import list_halftime_score_candidates, load_json
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs, resolve_all_odds_dir


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch half-time + full-time scores for past matches."
    )
    parser.add_argument(
        "--date",
        required=True,
        help="ISO date to process, e.g. 2026-05-08",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max matches to process. 0 = all. Default: 10.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=5,
        help="Parallel tabs per browser round. Default: 5.",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "firefox", "edge"],
        default=None,
        help="Override configured browser.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List candidates only — do not open browser or write anything.",
    )
    parser.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip Supabase upload even if credentials are configured.",
    )
    parser.add_argument(
        "--base-dir",
        help="Override data root. Defaults to _headless_output in cwd.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    load_env_from_assets()

    base_dir = (
        Path(str(args.base_dir)).expanduser()
        if args.base_dir
        else (Path.cwd() / "_headless_output")
    ).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)

    os.environ["SOCCER_PLACE_DATA_DIR"] = str(base_dir)
    os.environ["SOCCER_SCENT_RAW_DIR"] = str(base_dir / "data" / "raw")

    config = ConfigManager()
    ensure_app_dirs(config)

    date_iso = args.date.strip()
    all_odds_dir = resolve_all_odds_dir(config)
    json_path = all_odds_dir / f"{date_iso}.json"

    if not json_path.exists():
        _log(f"ERROR: No all_odds file found for {date_iso}: {json_path}")
        return 1

    payload = load_json(json_path)
    candidates = list_halftime_score_candidates(
        payload,
        limit=args.limit,
        date_iso=date_iso,
        buffer_hours=3,
    )

    _log(f"Date: {date_iso}  |  Candidates (limit={args.limit}): {len(candidates)}")
    for i, c in enumerate(candidates, 1):
        _log(f"  [{i:3d}] {c['match_id']}  {c['home']} vs {c['away']}  {c['url'][-40:]}")

    if not candidates:
        _log("No candidates — nothing to do.")
        return 0

    if args.dry_run:
        _log("Dry run — exiting without fetching.")
        return 0

    supabase = None if args.no_upload else SupabaseManager(config=config)

    fetcher = SeleniumPageSourceFetcher(config=config, browser_name=args.browser)
    pipeline = AllOddsPipeline(
        config=config,
        page_source_fetcher=fetcher,
        supabase_manager=supabase,
    )

    _log(f"Starting fetch: batch_size={args.batch_size} ...")
    with fetcher:
        result = pipeline.run_halftime_score_refresh(
            date_iso,
            limit=args.limit,
            batch_size=args.batch_size,
            persist=True,
        )

    _log(
        f"Done — candidates={result['candidates']} processed={result['processed']} "
        f"updated={result['updated']} failed={result['failed']}"
    )

    # Print a sample of what was stored
    updated_payload = load_json(json_path)
    _log("Sample scores stored:")
    shown = 0
    for c in candidates:
        mid = c["match_id"]
        item = (updated_payload.get("matches") or {}).get(mid) or {}
        scores = item.get("scores") or {}
        if scores:
            _log(
                f"  {mid} {c['home']} vs {c['away']}: "
                f"FT {scores.get('ft_home')}-{scores.get('ft_away')}  "
                f"1H {scores.get('1h_home')}-{scores.get('1h_away')}  "
                f"2H {scores.get('2h_home')}-{scores.get('2h_away')}"
            )
            shown += 1
            if shown >= 5:
                break
    if shown == 0:
        _log("  (no scores stored — check logs above for parse errors)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
