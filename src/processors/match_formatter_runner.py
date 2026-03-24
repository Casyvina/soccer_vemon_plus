from formatters.excel_match_formatter_v2 import ExcelMatchFormatterV2
import pandas as pd


class MatchFormatterRunner:
    def __init__(self, raw_matches: list[dict], logger=None):
        self.raw_matches = raw_matches
        self.logger = logger

    def _log(self, message: str, level: str = "WARNING"):
        if self.logger is not None:
            try:
                self.logger.log(message, level)
                return
            except Exception:
                pass
        print(message)

    def _validate_match(self, match) -> tuple[bool, str]:
        if not isinstance(match, dict):
            return False, "match is not a dict"

        md = match.get("match_details")
        if not isinstance(md, dict) or not md:
            return False, "missing match_details"

        home = (md.get("home_team") or "").strip()
        away = (md.get("away_team") or "").strip()
        if not home or not away:
            return False, "missing home_team/away_team"

        return True, ""

    def process(self) -> pd.DataFrame:
        all_rows = []  # Use local list, not self.rows
        for i, match in enumerate(self.raw_matches):
            ok, reason = self._validate_match(match)
            if not ok:
                url = None
                try:
                    url = match.get("url") if isinstance(match, dict) else None
                except Exception:
                    url = None

                self._log(
                    f"⚠️ Skipping invalid match payload ({reason}): {url or 'unknown'}",
                    "WARNING",
                )
                continue

            try:
                match["index"] = i + 1
                formatter = ExcelMatchFormatterV2(match)  # New instance per match
                rows = formatter.build_rows()
                all_rows.extend(rows)
            except Exception as e:
                self._log(
                    f"❌ Failed to format match {match.get('url', 'unknown')}: {e}",
                    "ERROR",
                )
        return pd.DataFrame(all_rows)

    
    
    
