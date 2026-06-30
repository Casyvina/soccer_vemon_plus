"""
Fetch and upload scores for raw match JSONs that have no all_odds file.

Walks _headless_output/data/raw/YYYY-MM-DD/ directories, skips any date
that already has an all_odds JSON (those are handled by headless_score_cli.py),
then fetches summary pages and pushes scores to Supabase.

Usage:
    python src/fix_raw_scores.py --browser chrome
    python src/fix_raw_scores.py --browser chrome --dry-run
    python src/fix_raw_scores.py --browser chrome --date 2026-03-17
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
from headless.parsers.match_summary import parse_match_summary
from headless.selenium_fetch import SeleniumPageSourceFetcher
from utils.all_odds_store import extract_match_id
from utils.env_loader import load_env_from_assets
from utils.paths import ensure_app_dirs


def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


def _collect_candidates(raw_root: Path, all_odds_root: Path, only_date: str = "") -> list[dict]:
    """
    Walk raw/YYYY-MM-DD/ directories and collect match entries missing from all_odds.
    Returns list of {date_iso, match_id, url, home, away}.
    """
    candidates = []
    for date_dir in sorted(raw_root.iterdir()):
        if not date_dir.is_dir():
            continue
        date_iso = date_dir.name
        # Skip the all_odds subdirectory itself
        if date_iso == "all_odds":
            continue
        # Validate looks like a date
        if len(date_iso) != 10 or date_iso[4] != "-":
            continue
        if only_date and date_iso != only_date:
            continue

        all_odds_path = all_odds_root / f"{date_iso}.json"
        if all_odds_path.exists():
            _log(f"  {date_iso}: all_odds exists — skipping (use headless_score_cli.py)")
            continue

        for match_file in sorted(date_dir.glob("*.json")):
            try:
                data = json.loads(match_file.read_text(encoding="utf-8"))
            except Exception:
                continue

            url = str(data.get("url") or "").strip()
            if not url:
                continue

            match_id = extract_match_id(url) or match_file.stem
            details = data.get("match_details") or {}
            home = str(details.get("home_team") or "").strip()
            away = str(details.get("away_team") or "").strip()

            candidates.append({
                "date_iso": date_iso,
                "match_id": match_id,
                "url": url,
                "home": home,
                "away": away,
            })

    return candidates


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch scores for raw match JSONs that have no all_odds file."
    )
    parser.add_argument("--date", default="", help="Process only this ISO date, e.g. 2026-03-17")
    parser.add_argument("--batch-size", type=int, default=3)
    parser.add_argument("--browser", choices=["chrome", "firefox", "edge"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--base-dir", default="")
    args = parser.parse_args(argv)

    load_env_from_assets()

    base_dir = (
        Path(str(args.base_dir)).expanduser()
        if args.base_dir
        else (Path.cwd() / "_headless_output")
    ).resolve()

    os.environ["SOCCER_PLACE_DATA_DIR"] = str(base_dir)
    os.environ["SOCCER_SCENT_RAW_DIR"] = str(base_dir / "data" / "raw")

    config = ConfigManager()
    ensure_app_dirs(config)

    raw_root = base_dir / "data" / "raw"
    all_odds_root = raw_root / "all_odds"

    _log("Scanning raw match directories for dates missing all_odds files...")
    candidates = _collect_candidates(raw_root, all_odds_root, only_date=args.date.strip())

    if not candidates:
        _log("No candidates found — nothing to do.")
        return 0

    # Group by date for display
    by_date: dict[str, list[dict]] = {}
    for c in candidates:
        by_date.setdefault(c["date_iso"], []).append(c)

    for date_iso, items in sorted(by_date.items()):
        _log(f"  {date_iso}: {len(items)} matches without all_odds")
        for c in items:
            _log(f"    {c['match_id']}  {c['home']} vs {c['away']}")

    _log(f"Total: {len(candidates)} matches across {len(by_date)} dates")

    if args.dry_run:
        _log("Dry run — exiting without fetching.")
        return 0

    supabase = None if args.no_upload else SupabaseManager(config=config)
    fetcher = SeleniumPageSourceFetcher(config=config, browser_name=args.browser)

    batch_size = max(1, args.batch_size)
    total_processed = 0
    total_updated = 0
    total_failed = 0

    with fetcher:
        for i in range(0, len(candidates), batch_size):
            batch = candidates[i: i + batch_size]
            items = [(c["match_id"], c["url"]) for c in batch]

            try:
                pages = fetcher.fetch_summary_pages(items)
            except Exception as e:
                _log(f"Batch fetch failed: {e}")
                total_failed += len(batch)
                total_processed += len(batch)
                continue

            for c in batch:
                mid = c["match_id"]
                total_processed += 1
                try:
                    html = pages.get(mid) or ""
                    summary = parse_match_summary(html)
                    scores = {
                        "1h_home": summary.get("1h_home"),
                        "1h_away": summary.get("1h_away"),
                        "2h_home": summary.get("2h_home"),
                        "2h_away": summary.get("2h_away"),
                        "ft_home": summary.get("ft_home"),
                        "ft_away": summary.get("ft_away"),
                    }
                    has_score = any(v for v in scores.values() if v not in (None, ""))
                    if has_score:
                        total_updated += 1
                        _log(
                            f"  {mid} {c['home']} vs {c['away']}: "
                            f"FT {scores.get('ft_home')}-{scores.get('ft_away')}  "
                            f"1H {scores.get('1h_home')}-{scores.get('1h_away')}"
                        )
                    else:
                        _log(f"  {mid} {c['home']} vs {c['away']}: no scores parsed")

                    if supabase and has_score:
                        supabase.upsert_score(
                            match_id=mid,
                            date_iso=c["date_iso"],
                            url=c["url"],
                            home=c["home"],
                            away=c["away"],
                            scores=scores,
                        )
                except Exception as e:
                    _log(f"  {mid} failed: {e}")
                    total_failed += 1

    _log(
        f"Done — processed={total_processed} updated={total_updated} failed={total_failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
