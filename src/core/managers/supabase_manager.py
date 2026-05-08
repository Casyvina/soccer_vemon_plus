from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any, Optional
from urllib.parse import urlparse


def _format_day_label(date_iso: str) -> str:
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        return dt.strftime("%a %d %b %Y")
    except Exception:
        return date_iso

from supabase import create_client, Client


class SupabaseManager:
    def __init__(self, config=None):
        self.config = config
        self.url = ""
        self.key = ""
        self.client: Optional[Client] = None
        self.refresh_client(force=True)

    def _log(self, message: str) -> None:
        text = str(message or "")
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("ascii", "replace").decode("ascii"))

    def _config_get(self, section: str, key: str) -> str:
        try:
            return str(self.config.get(section, key, default="") or "").strip()
        except Exception:
            return ""

    def _normalize_supabase_url(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        if "://" not in text:
            text = f"https://{text}"
        try:
            parsed = urlparse(text)
            scheme = parsed.scheme or "https"
            host = (parsed.hostname or "").strip()
            if not host:
                return text.rstrip("/")
            port = f":{parsed.port}" if parsed.port else ""
            return f"{scheme}://{host}{port}"
        except Exception:
            return text.rstrip("/")

    def _load_credentials(self) -> tuple[str, str]:
        url = (
            os.getenv("SUPABASE_URL_LF")
            or self._config_get("supabase", "url")
            or ""
        )
        key = (
            os.getenv("SUPABASE_SERVICE_KEY_LF")
            or os.getenv("SUPABASE_KEY")
            or self._config_get("supabase", "service_key")
            or self._config_get("supabase", "key")
            or ""
        )
        return self._normalize_supabase_url(url), str(key or "").strip()

    def refresh_client(self, force: bool = False) -> bool:
        next_url, next_key = self._load_credentials()
        same = next_url == self.url and next_key == self.key
        if not force and self.client and same:
            return True

        self.url = next_url
        self.key = next_key

        if not self.url or not self.key:
            self.client = None
            self._log("Supabase URL/key missing — Supabase disabled.")
            return False

        try:
            self.client = create_client(self.url, self.key)
            self._log("Supabase client initialized.")
            return True
        except Exception as e:
            self.client = None
            self._log(f"Supabase init failed: {e}")
            return False

    def _safe_identifier(self, name: str, fallback: str) -> str:
        candidate = (name or "").strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate or ""):
            return candidate
        return fallback

    def _is_transient_network_error(self, error: Any) -> bool:
        text = str(error).lower()
        return any(
            token in text
            for token in (
                "sslv3 alert bad record mac",
                "bad record mac",
                "connection reset",
                "connection aborted",
                "remote end closed connection",
                "server disconnected",
                "eof occurred in violation of protocol",
                "read timed out",
                "write timed out",
                "network is unreachable",
                "temporary failure in name resolution",
                "failed to establish a new connection",
                "503 service unavailable",
                "502 bad gateway",
                "max retries exceeded",
            )
        )

    def _upsert_with_retry(
        self,
        table_name: str,
        payload: Any,
        on_conflict: str,
        label: str,
        max_attempts: int = 3,
        base_sleep_seconds: float = 1.2,
    ) -> tuple[bool, str]:
        if not self.refresh_client(force=False):
            return False, "Supabase is not configured."

        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                self.client.table(table_name).upsert(
                    payload, on_conflict=on_conflict
                ).execute()
                return True, ""
            except Exception as e:
                last_error = str(e)
                if attempt < max_attempts and self._is_transient_network_error(e):
                    wait = base_sleep_seconds * attempt
                    self._log(
                        f"{label} network error ({attempt}/{max_attempts}): {e}. "
                        f"Retrying in {wait:.1f}s"
                    )
                    self.refresh_client(force=True)
                    time.sleep(wait)
                    continue
                return False, last_error

        return False, last_error

    def upsert_all_odds_snapshot(
        self, date_iso: str, payload: dict, table: Optional[str] = None
    ) -> bool:
        if not self.client:
            return False

        date_key = (date_iso or "").strip()
        if not date_key or date_key == "unknown":
            return False

        table_name = self._safe_identifier(
            table
            or self._config_get("supabase", "all_odds_snapshots_table")
            or "all_odds_snapshots",
            "all_odds_snapshots",
        )
        row = {
            "date": date_key,
            "data": payload,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        ok, err = self._upsert_with_retry(
            table_name=table_name,
            payload=row,
            on_conflict="date",
            label=f"all_odds snapshot {date_key}",
        )
        if ok:
            self._log(f"Supabase: all_odds snapshot uploaded ({date_key}).")
        else:
            self._log(f"Supabase: all_odds snapshot failed ({date_key}): {err}")
        return ok

    def upsert_market_day_from_odds(
        self, date_iso: str, matches: dict, table: Optional[str] = None
    ) -> bool:
        """Upsert a market_days row built from the all_odds snapshot matches dict."""
        if not self.client:
            return False

        date_key = (date_iso or "").strip()
        if not date_key or date_key == "unknown":
            return False

        payload_rows = []
        for match_id, m in (matches or {}).items():
            if not isinstance(m, dict):
                continue
            payload_rows.append({
                "matchId": match_id,
                "kickoffTime": str(m.get("time") or ""),
                "homeTeam": str(m.get("home") or ""),
                "awayTeam": str(m.get("away") or ""),
                "country": str(m.get("country") or ""),
                "competition": str(m.get("competition") or ""),
                "odds": m.get("odds") or {},
            })

        payload_rows.sort(key=lambda x: (x.get("kickoffTime", ""), x.get("homeTeam", "")))

        table_name = self._safe_identifier(
            table or self._config_get("supabase", "market_days_table") or "market_days",
            "market_days",
        )
        row = {
            "id": date_key,
            "label": _format_day_label(date_key),
            "match_count": len(payload_rows),
            "payload": payload_rows,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        ok, err = self._upsert_with_retry(
            table_name=table_name,
            payload=row,
            on_conflict="id",
            label=f"market_day {date_key}",
        )
        if ok:
            self._log(f"Supabase: market_days uploaded ({date_key}, {len(payload_rows)} matches).")
        else:
            self._log(f"Supabase: market_days failed ({date_key}): {err}")
        return ok

    def upsert_score(
        self,
        match_id: str,
        date_iso: str,
        url: str = "",
        home: str = "",
        away: str = "",
        scores: Optional[dict] = None,
        table: Optional[str] = None,
    ) -> bool:
        if not self.client:
            return False

        match_id = (match_id or "").strip()
        if not match_id:
            return False

        table_name = self._safe_identifier(
            table
            or self._config_get("supabase", "scores_table")
            or "scores",
            "scores",
        )
        scores = scores or {}
        row = {
            "id": match_id,
            "match_url": url or None,
            "date": date_iso if date_iso and date_iso != "unknown" else None,
            "home": home or None,
            "away": away or None,
            "fh_home": scores.get("1h_home"),
            "fh_away": scores.get("1h_away"),
            "sh_home": scores.get("2h_home"),
            "sh_away": scores.get("2h_away"),
            "ft_home": scores.get("ft_home"),
            "ft_away": scores.get("ft_away"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        ok, err = self._upsert_with_retry(
            table_name=table_name,
            payload=row,
            on_conflict="id",
            label=f"score {match_id}",
        )
        if not ok:
            self._log(f"Supabase: score upsert failed ({match_id}): {err}")
        return ok
