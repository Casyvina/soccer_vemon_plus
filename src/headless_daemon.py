"""
VM daemon — runs forever, orchestrating three phases in a loop:

  1. Odds fetch   — fetch next N days odds pages; recheck every --recheck-hours
  2. Details fetch — fetch match details for all pending matches across open days
  3. HT scores    — refresh half-time scores for completed matches (while idle)

Start with:
    python src/headless_daemon.py
    python src/headless_daemon.py --days-ahead 3 --recheck-hours 4 --db-batch-size 50

Logs to stdout — redirect to a file if running unattended:
    nohup python src/headless_daemon.py >> logs/daemon.log 2>&1 &
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from core.managers.config_manager import ConfigManager
from core.managers.supabase_manager import SupabaseManager
from headless.odds_fetch import SeleniumOddsPageFetcher
from headless.pipeline.all_odds_pipeline import AllOddsPipeline
from headless.pipeline.match_pipeline import MatchPipeline
from headless.selenium_fetch import SeleniumPageSourceFetcher
from utils.all_odds_store import (
    _parse_kickoff_datetime,
    list_detail_candidates,
    list_halftime_score_candidates,
    load_json,
    mark_details_failed_in_payload,
    mark_details_fetched_in_payload,
    save_json,
)
from utils.env_loader import load_env_from_assets
from utils.market_payload_builder import _format_market_day_label, build_market_match
from utils.notifier import LeagueFluxNotifier, NtfyNotifier
from utils.paths import ensure_app_dirs, resolve_all_odds_dir
from utils.signal_derive import combo_key, derive_signal_code, signal_complete


# ── logging ──────────────────────────────────────────────────────────────────

def _log(level: str, message: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level.upper():5}] {message}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"), flush=True)


# ── daemon ────────────────────────────────────────────────────────────────────

class VmDaemon:
    def __init__(
        self,
        config,
        base_dir: Path,
        *,
        days_ahead: int = 3,
        recheck_hours: float = 4.0,
        idle_sleep_mins: float = 30.0,
        db_batch_size: int = 50,
        detail_max_attempts: int = 3,
        ht_lookback_days: int = 7,
        browser: str | None = None,
        leagueflux_url: str = "",
        leagueflux_notify_secret: str = "",
        ntfy_url: str = "",
        ntfy_token: str = "",
        alert_lead_mins: int = 30,
    ):
        self.config = config
        self.base_dir = base_dir
        self.days_ahead = max(1, days_ahead)
        self.recheck_interval = timedelta(hours=max(0.5, recheck_hours))
        self.idle_sleep_secs = max(60, int(idle_sleep_mins * 60))
        self.db_batch_size = max(0, db_batch_size)
        self.detail_max_attempts = max(1, detail_max_attempts)
        self.ht_lookback_days = max(0, ht_lookback_days)
        self.browser = browser
        self.supabase_manager = SupabaseManager(config=config)
        self._state_path = base_dir / "daemon_state.json"
        self._state: dict = self._load_state()
        # Alerts — LeagueFlux Web Push is primary; ntfy is fallback/testing
        self.lf_notifier = LeagueFluxNotifier(base_url=leagueflux_url, secret=leagueflux_notify_secret)
        self.ntfy_notifier = NtfyNotifier(url=ntfy_url, token=ntfy_token)
        self.alert_lead_mins = max(5, alert_lead_mins)  # fire this many minutes before kick-off

    # ── public ────────────────────────────────────────────────────────────────

    def run(self) -> None:
        _log("info", f"Daemon started — days_ahead={self.days_ahead} "
             f"recheck={self.recheck_interval} idle={self.idle_sleep_secs}s")
        while True:
            try:
                self._cycle()
            except KeyboardInterrupt:
                _log("info", "Daemon stopped.")
                return
            except Exception as exc:
                _log("error", f"Unhandled cycle error: {exc}")
                time.sleep(60)

    # ── cycle ─────────────────────────────────────────────────────────────────

    def _cycle(self) -> None:
        now = datetime.now()

        # Phase 1 — Odds fetch
        due_offsets = self._offsets_due_for_odds(now)
        if due_offsets:
            self._run_odds_phase(due_offsets, now)

        # Phase 2 — Details fetch
        pending_dates = self._get_pending_detail_dates()
        if pending_dates:
            self._run_details_phase(pending_dates)

        # Phase 3 — HT scores (only when details queue is empty)
        if not self._get_pending_detail_dates():
            self._run_ht_phase()

        # Phase 4 — Match alerts
        if self.lf_notifier.configured or self.ntfy_notifier.configured:
            self._run_alert_phase()

        # Sleep — wake early if an alert window is approaching
        sleep_secs = self._seconds_until_next_trigger(datetime.now())
        _log("info", f"Cycle complete — sleeping {sleep_secs // 60:.0f} min")
        self._save_state()
        time.sleep(sleep_secs)

    # ── phase 1: odds ─────────────────────────────────────────────────────────

    def _offsets_due_for_odds(self, now: datetime) -> list[int]:
        due: list[int] = []
        for offset in range(self.days_ahead):
            date_iso = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            last_str = self._state.get("last_odds_fetch", {}).get(date_iso)
            if last_str is None:
                due.append(offset)
                continue
            try:
                if (now - datetime.fromisoformat(last_str)) >= self.recheck_interval:
                    due.append(offset)
            except Exception:
                due.append(offset)
        return due

    def _run_odds_phase(self, offsets: list[int], now: datetime) -> None:
        _log("info", f"Odds phase: offsets {offsets}")
        try:
            fetcher = SeleniumOddsPageFetcher(config=self.config, browser_name=self.browser)
            pipeline = AllOddsPipeline(
                config=self.config,
                page_source_fetcher=fetcher,
                supabase_manager=self.supabase_manager,
            )
            results = pipeline.run_for_days(offsets, save_html=False, save_json_payload=True)
            for result in results:
                self._state.setdefault("last_odds_fetch", {})[result.date_iso] = (
                    now.isoformat(timespec="seconds")
                )
                _log("info",
                     f"  Odds {result.date_iso}: {result.match_count} matches "
                     f"added={result.merge_stats.get('added', 0)} "
                     f"updated={result.merge_stats.get('updated', 0)}")
        except Exception as exc:
            _log("error", f"Odds phase error: {exc}")

    # ── phase 2: details ──────────────────────────────────────────────────────

    def _get_pending_detail_dates(self) -> list[str]:
        now = datetime.now()
        all_odds_dir = resolve_all_odds_dir(self.config)
        dates: list[str] = []
        # Check past 2 days + days_ahead into the future
        for offset in range(-2, self.days_ahead):
            date_iso = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            path = all_odds_dir / f"{date_iso}.json"
            if not path.exists():
                continue
            payload = load_json(path)
            if list_detail_candidates(
                payload,
                include_fetched=False,
                only_failed=False,
                max_attempts=self.detail_max_attempts,
            ):
                dates.append(date_iso)
        return dates

    def _run_details_phase(self, date_isos: list[str]) -> None:
        _log("info", f"Details phase: {date_isos}")
        all_odds_dir = resolve_all_odds_dir(self.config)

        match_fetcher = SeleniumPageSourceFetcher(config=self.config, browser_name=self.browser)
        pipeline = MatchPipeline(
            config=self.config,
            page_source_fetcher=match_fetcher,
            cache_enabled=True,
            max_cache_entries=60,
        )

        with match_fetcher:
            for date_iso in date_isos:
                path = all_odds_dir / f"{date_iso}.json"
                payload = load_json(path)
                candidates = list_detail_candidates(
                    payload,
                    include_fetched=False,
                    only_failed=False,
                    max_attempts=self.detail_max_attempts,
                )
                _log("info", f"  Details {date_iso}: {len(candidates)} pending")

                market_batch: list[dict] = []

                def _flush(d_iso: str = date_iso, batch: list = market_batch) -> None:
                    if not batch:
                        return
                    try:
                        self.supabase_manager.upsert_market_day({
                            "id": d_iso,
                            "label": _format_market_day_label(d_iso),
                            "matches": list(batch),
                        })
                        _log("info", f"  DB flush: {len(batch)} match(es) → {d_iso}")
                    except Exception as exc:
                        _log("error", f"  DB flush error: {exc}")
                    batch.clear()

                success = 0
                failed = 0
                restart_every = 10
                for i, candidate in enumerate(candidates):
                    match_id = candidate["match_id"]
                    url = candidate["url"]
                    try:
                        result = pipeline.run_from_url(url, save_html=False, save_json=True)
                        mark_details_fetched_in_payload(payload, match_id, True)
                        save_json(path, payload)
                        success += 1

                        try:
                            mid = result.match_id or match_id
                            market_batch.append(
                                build_market_match(result.payload, date_iso, mid)
                            )
                            if self.db_batch_size > 0 and len(market_batch) >= self.db_batch_size:
                                _flush()
                        except Exception:
                            pass

                    except Exception as exc:
                        _log("error", f"  Detail failed {match_id}: {exc}")
                        mark_details_failed_in_payload(payload, match_id, str(exc))
                        save_json(path, payload)
                        failed += 1

                    if restart_every > 0 and (i + 1) % restart_every == 0 and (i + 1) < len(candidates):
                        _log("info", f"  Restarting browser after {restart_every} matches...")
                        match_fetcher.close()
                        match_fetcher.open()
                        pipeline.reset_cache()

                _flush()
                _log("info", f"  Details {date_iso} done: ok={success} failed={failed}")

    # ── phase 3: HT scores ────────────────────────────────────────────────────

    def _run_ht_phase(self) -> None:
        now = datetime.now()
        all_odds_dir = resolve_all_odds_dir(self.config)

        # Collect dates that have HT candidates
        ht_dates: list[str] = []
        for offset in range(-self.ht_lookback_days, 1):
            date_iso = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            path = all_odds_dir / f"{date_iso}.json"
            if not path.exists():
                continue
            payload = load_json(path)
            if list_halftime_score_candidates(payload, limit=1):
                ht_dates.append(date_iso)

        if not ht_dates:
            return

        _log("info", f"HT scores phase: {ht_dates}")

        ht_fetcher = SeleniumPageSourceFetcher(config=self.config, browser_name=self.browser)
        ht_pipeline = AllOddsPipeline(
            config=self.config,
            page_source_fetcher=ht_fetcher,
            supabase_manager=self.supabase_manager,
        )

        with ht_fetcher:
            for date_iso in ht_dates:
                try:
                    summary = ht_pipeline.run_halftime_score_refresh(date_iso, persist=True)
                    _log("info",
                         f"  HT {date_iso}: candidates={summary['candidates']} "
                         f"updated={summary['updated']} failed={summary['failed']}")
                except Exception as exc:
                    _log("error", f"  HT {date_iso} error: {exc}")

    # ── phase 4: match alerts ─────────────────────────────────────────────────

    def _run_alert_phase(self) -> None:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        all_odds_dir = resolve_all_odds_dir(self.config)
        path = all_odds_dir / f"{today}.json"
        if not path.exists():
            return

        payload = load_json(path)
        matches = payload.get("matches") or {}
        if not matches:
            return

        alerted_today: set[str] = set(
            self._state.get("alerts_sent", {}).get(today, [])
        )
        new_alerts: list[str] = []

        for match_id, m in matches.items():
            if match_id in alerted_today:
                continue

            # Skip already-started or terminal matches
            status = str(m.get("status") or "").strip().lower()
            if status and status not in ("scheduled", "not started", ""):
                continue

            kickoff = _parse_kickoff_datetime(today, str(m.get("time") or ""))
            if kickoff is None:
                continue

            mins_until = (kickoff - now).total_seconds() / 60
            # Fire when within (lead_mins + 5) down to 5 minutes before kick-off
            window_hi = self.alert_lead_mins + 5
            window_lo = 5
            if not (window_lo <= mins_until <= window_hi):
                continue

            # Build notification content
            home = str(m.get("home") or "?")
            away = str(m.get("away") or "?")
            competition = str(m.get("competition") or m.get("country") or "")
            odds = m.get("odds") or {}
            h_odds = str(odds.get("1") or odds.get("1b") or "").strip()
            a_odds = str(odds.get("2") or odds.get("2b") or "").strip()
            odds_text = f" · {h_odds}/{a_odds}" if h_odds and a_odds else ""

            signal_text = ""
            fh_text = ""

            if self.supabase_manager.client:
                try:
                    res = (
                        self.supabase_manager.client
                        .from_("market_matches")
                        .select("payload")
                        .eq("match_id", match_id)
                        .maybe_single()
                        .execute()
                    )
                    if res.data:
                        mp = res.data.get("payload") or {}
                        chart, reality, h2h = derive_signal_code(mp)
                        if signal_complete(chart, reality, h2h):
                            key = combo_key(chart, reality, h2h)  # type: ignore[arg-type]
                            signal_text = f" · {key}"

                            # Determine fav from odds
                            try:
                                hf = float(h_odds) if h_odds else 0.0
                                af = float(a_odds) if a_odds else 0.0
                                home_fav = hf > 0 and af > 0 and hf < af
                            except ValueError:
                                home_fav = True

                            vault_res = (
                                self.supabase_manager.client
                                .from_("signal_vault_master")
                                .select("market_flags,sample_count")
                                .eq("combo_key", key)
                                .eq("home_fav", home_fav)
                                .maybe_single()
                                .execute()
                            )
                            if vault_res.data:
                                flags = vault_res.data.get("market_flags") or {}
                                fh_key = "FH-X2" if home_fav else "FH-1X"
                                fh_rate = flags.get(fh_key)
                                samples = vault_res.data.get("sample_count") or 0
                                if fh_rate is not None:
                                    fh_text = f" · {fh_key} {fh_rate}% ({samples}m)"
                except Exception as exc:
                    _log("warn", f"Alert signal lookup failed for {match_id}: {exc}")

            mins_label = f"in {int(mins_until)}min"
            title = f"{home} vs {away} {mins_label}"
            body = f"{competition}{odds_text}{signal_text}{fh_text}"

            # Try LeagueFlux Web Push first; fall back to ntfy
            if self.lf_notifier.configured:
                ok = self.lf_notifier.send(title, body)
            else:
                ok = self.ntfy_notifier.send(title, body, priority="high", tags=["soccer"])

            if ok:
                new_alerts.append(match_id)
                _log("info", f"Alert sent: {home} vs {away} {mins_label}{signal_text}{fh_text}")
            else:
                _log("warn", f"Alert send failed: {match_id} ({home} vs {away})")

        if new_alerts:
            self._state.setdefault("alerts_sent", {})
            existing = self._state["alerts_sent"].get(today, [])
            self._state["alerts_sent"][today] = list(set(existing + new_alerts))
            # Prune entries older than 7 days
            cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            self._state["alerts_sent"] = {
                d: v for d, v in self._state["alerts_sent"].items() if d >= cutoff
            }
            self._save_state()

    # ── sleep logic ───────────────────────────────────────────────────────────

    def _seconds_until_next_trigger(self, now: datetime) -> int:
        # Find when the earliest odds recheck is due
        earliest = int(self.recheck_interval.total_seconds())
        for last_str in self._state.get("last_odds_fetch", {}).values():
            try:
                last = datetime.fromisoformat(last_str)
                remaining = int((last + self.recheck_interval - now).total_seconds())
                earliest = min(earliest, max(0, remaining))
            except Exception:
                pass

        # Wake early if an alert window is approaching for today's matches
        if self.lf_notifier.configured or self.ntfy_notifier.configured:
            alert_wake = self._seconds_until_next_alert_window(now)
            if alert_wake is not None:
                earliest = min(earliest, alert_wake)

        return max(60, min(earliest, self.idle_sleep_secs))

    def _seconds_until_next_alert_window(self, now: datetime) -> int | None:
        """
        Returns seconds until we should wake up to catch the next unalerted match.
        Returns None if there are no upcoming matches to alert on.
        """
        today = now.strftime("%Y-%m-%d")
        all_odds_dir = resolve_all_odds_dir(self.config)
        path = all_odds_dir / f"{today}.json"
        if not path.exists():
            return None

        payload = load_json(path)
        matches = payload.get("matches") or {}
        alerted_today: set[str] = set(
            self._state.get("alerts_sent", {}).get(today, [])
        )

        soonest: int | None = None
        for match_id, m in matches.items():
            if match_id in alerted_today:
                continue
            status = str(m.get("status") or "").strip().lower()
            if status and status not in ("scheduled", "not started", ""):
                continue
            kickoff = _parse_kickoff_datetime(today, str(m.get("time") or ""))
            if kickoff is None:
                continue
            # We want to be awake at (kickoff - lead_mins)
            wake_at = kickoff - timedelta(minutes=self.alert_lead_mins + 3)
            secs = int((wake_at - now).total_seconds())
            if secs <= 0:
                # Already in window or past — wake immediately (next cycle)
                return 60
            if soonest is None or secs < soonest:
                soonest = secs
        return soonest

    # ── state persistence ─────────────────────────────────────────────────────

    def _load_state(self) -> dict:
        try:
            if self._state_path.exists():
                return json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_state(self) -> None:
        try:
            self._state["daemon_last_save"] = datetime.now().isoformat(timespec="seconds")
            self._state_path.write_text(
                json.dumps(self._state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Long-running VM daemon: odds → details → HT scores, forever."
    )
    parser.add_argument(
        "--days-ahead",
        type=int,
        default=3,
        help="Number of future days to fetch odds for (today=1, tomorrow=2, etc.). Default: 3.",
    )
    parser.add_argument(
        "--recheck-hours",
        type=float,
        default=4.0,
        help="How often (hours) to re-fetch odds for a date to catch late Flashscore uploads. Default: 4.",
    )
    parser.add_argument(
        "--idle-sleep-mins",
        type=float,
        default=30.0,
        help="Minutes to sleep between cycles when nothing is pending. Default: 30.",
    )
    parser.add_argument(
        "--db-batch-size",
        type=int,
        default=50,
        help="Match details to accumulate before a Supabase flush. 0 = flush only at end of each date. Default: 50.",
    )
    parser.add_argument(
        "--detail-max-attempts",
        type=int,
        default=3,
        help="Skip detail candidates that have failed this many times. Default: 3.",
    )
    parser.add_argument(
        "--ht-lookback-days",
        type=int,
        default=7,
        help="How many past days to scan for missing HT scores. Default: 7.",
    )
    parser.add_argument(
        "--browser",
        choices=["chrome", "firefox", "edge"],
        help="Override the configured browser.",
    )
    parser.add_argument(
        "--base-dir",
        help="Override the data root. Defaults to _headless_output in the current directory.",
    )
    parser.add_argument(
        "--leagueflux-url",
        default="",
        help=(
            "LeagueFlux base URL, e.g. https://leagueflux.com. "
            "Falls back to LEAGUEFLUX_URL env var. When set, alerts go via Web Push."
        ),
    )
    parser.add_argument(
        "--leagueflux-notify-secret",
        default="",
        help="Shared secret for /api/notifications/send (NOTIFICATION_API_SECRET on Vercel). Falls back to LEAGUEFLUX_NOTIFY_SECRET env var.",
    )
    parser.add_argument(
        "--ntfy-url",
        default="",
        help="ntfy topic URL (fallback / testing). Falls back to NTFY_URL env var.",
    )
    parser.add_argument(
        "--ntfy-token",
        default="",
        help="Bearer token for ntfy server. Falls back to NTFY_TOKEN env var.",
    )
    parser.add_argument(
        "--alert-lead-mins",
        type=int,
        default=30,
        help="Send alert this many minutes before kick-off. Default: 30.",
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

    daemon = VmDaemon(
        config=config,
        base_dir=base_dir,
        days_ahead=args.days_ahead,
        recheck_hours=args.recheck_hours,
        idle_sleep_mins=args.idle_sleep_mins,
        db_batch_size=args.db_batch_size,
        detail_max_attempts=args.detail_max_attempts,
        ht_lookback_days=args.ht_lookback_days,
        browser=args.browser,
        leagueflux_url=args.leagueflux_url,
        leagueflux_notify_secret=args.leagueflux_notify_secret,
        ntfy_url=args.ntfy_url,
        ntfy_token=args.ntfy_token,
        alert_lead_mins=args.alert_lead_mins,
    )
    daemon.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
