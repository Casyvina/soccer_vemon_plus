from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from headless.league_fetch import SeleniumLeaguePageFetcher
from headless.parsers.league_results import (
    build_fixtures_url,
    normalize_results_url,
    parse_league_header,
    parse_league_rows,
)
from utils.export import export_excel_and_csv_from_json, export_league_data
from utils.paths import resolve_processed_dir


@dataclass
class LeaguePipelineResult:
    results_url: str
    fixtures_url: str
    html_paths: dict[str, str]
    json_path: str
    excel_path: str
    csv_path: str
    payload: dict


class _PipelineLogger:
    def log(self, message: str, level: str = "INFO"):
        text = f"[{level}] {message}"
        try:
            print(text)
        except UnicodeEncodeError:
            safe = text.encode("ascii", errors="replace").decode("ascii")
            print(safe)


class _PipelineStore:
    def __init__(self, config=None):
        self.config = config
        self.logger = _PipelineLogger()


class LeaguePipeline:
    def __init__(self, config=None, page_source_fetcher=None):
        self.config = config
        self.page_source_fetcher = page_source_fetcher or SeleniumLeaguePageFetcher(
            config=config
        )

    def run_from_url(
        self,
        url: str,
        *,
        save_html: bool = True,
        save_json: bool = True,
        export_sheets: bool = False,
    ) -> LeaguePipelineResult:
        results_url = normalize_results_url(url)
        if not results_url:
            raise ValueError("Invalid league results URL.")
        fixtures_url = build_fixtures_url(results_url)

        pages = self.page_source_fetcher.fetch_pages(results_url, fixtures_url)
        results_html = str(pages.get("results") or "")
        fixtures_html = str(pages.get("fixtures") or "")

        header = parse_league_header(results_html or fixtures_html, results_url)
        results_rows = parse_league_rows(
            results_html,
            header=header,
            page_url=results_url,
            phase="result",
            start_index=1,
        )
        fixtures_rows = parse_league_rows(
            fixtures_html,
            header=header,
            page_url=fixtures_url,
            phase="fixture",
            start_index=(len(results_rows) + 1),
        )
        all_rows = results_rows + fixtures_rows

        if not all_rows:
            raise ValueError("No league rows were parsed from results/fixtures pages.")

        html_paths = (
            self._save_html_snapshots(
                header=header,
                pages={
                    "results": results_html,
                    "fixtures": fixtures_html,
                },
            )
            if save_html
            else {}
        )

        json_path = ""
        excel_path = ""
        csv_path = ""
        if save_json:
            store = _PipelineStore(config=self.config)
            json_path = export_league_data(header, all_rows, store)
            if export_sheets:
                excel_path, csv_path = export_excel_and_csv_from_json(
                    json_path, store=store
                )

        payload = {
            "meta": header,
            "rows": all_rows,
            "results_count": len(results_rows),
            "fixtures_count": len(fixtures_rows),
        }

        return LeaguePipelineResult(
            results_url=results_url,
            fixtures_url=fixtures_url,
            html_paths=html_paths,
            json_path=str(json_path or ""),
            excel_path=str(excel_path or ""),
            csv_path=str(csv_path or ""),
            payload=payload,
        )

    def _save_html_snapshots(self, *, header: dict, pages: dict[str, str]) -> dict[str, str]:
        root = (
            resolve_processed_dir(self.config)
            / "headless_league_html"
            / self._slug(header.get("country", "unknown"))
            / self._slug(header.get("competition", "unknown"))
            / self._safe_season(header.get("season", "unknown"))
        )
        root.mkdir(parents=True, exist_ok=True)

        saved: dict[str, str] = {}
        for key, html in pages.items():
            path = root / f"{key}.html"
            path.write_text(str(html or ""), encoding="utf-8")
            saved[key] = str(path)
        return saved

    @staticmethod
    def _slug(value: str) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "unknown"

    @staticmethod
    def _safe_season(value: str) -> str:
        text = str(value or "").strip().replace("/", "-")
        return re.sub(r'[\\:*?"<>|]+', "_", text) or "unknown"

    @staticmethod
    def to_serializable(result: LeaguePipelineResult) -> dict:
        return {
            "results_url": result.results_url,
            "fixtures_url": result.fixtures_url,
            "html_paths": result.html_paths,
            "json_path": result.json_path,
            "excel_path": result.excel_path,
            "csv_path": result.csv_path,
            "payload": result.payload,
        }

    @staticmethod
    def to_json(result: LeaguePipelineResult) -> str:
        return json.dumps(
            LeaguePipeline.to_serializable(result),
            indent=2,
            ensure_ascii=False,
        )
