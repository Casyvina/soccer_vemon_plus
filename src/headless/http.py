from __future__ import annotations

from typing import Iterable

import requests


class HeadlessHttpClient:
    DEFAULT_HEADERS = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: tuple[int, int] = (10, 30),
    ):
        self.session = session or requests.Session()
        self.session.headers.update(self.DEFAULT_HEADERS)
        self.timeout = timeout

    def fetch_text(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        if not response.encoding:
            response.encoding = "utf-8"
        return response.text

    def apply_selenium_cookies(self, cookies: Iterable[dict]) -> int:
        applied = 0
        for item in cookies or []:
            if not isinstance(item, dict):
                continue

            name = str(item.get("name") or "").strip()
            if not name:
                continue

            self.session.cookies.set(
                name,
                str(item.get("value") or ""),
                domain=item.get("domain") or None,
                path=item.get("path") or "/",
            )
            applied += 1
        return applied

    def set_user_agent(self, user_agent: str) -> None:
        value = str(user_agent or "").strip()
        if value:
            self.session.headers["User-Agent"] = value
