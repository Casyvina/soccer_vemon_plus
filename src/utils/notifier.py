"""
ntfy push notification sender.

Usage:
    notifier = NtfyNotifier(url="http://localhost/leagueflux-alerts", token="secret")
    notifier.send("Match starting", "Arsenal vs Chelsea in 28min · Signal O|AR|H · FH-X2 74%")

Configure via env vars or pass directly:
    NTFY_URL   — full topic URL e.g. http://77.42.70.63/leagueflux-alerts
    NTFY_TOKEN — optional bearer token (set in ntfy server config)
"""
from __future__ import annotations

import os

import requests


class NtfyNotifier:
    def __init__(self, url: str = "", token: str = ""):
        self.url = (url or os.getenv("NTFY_URL") or "").rstrip("/")
        self.token = token or os.getenv("NTFY_TOKEN") or ""

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def send(
        self,
        title: str,
        body: str,
        *,
        priority: str = "default",
        tags: list[str] | None = None,
        click: str = "",
    ) -> bool:
        if not self.configured:
            return False

        headers: dict[str, str] = {}
        if title:
            headers["Title"] = title
        if priority:
            headers["Priority"] = priority
        if tags:
            headers["Tags"] = ",".join(tags)
        if click:
            headers["Click"] = click
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            resp = requests.post(
                self.url,
                data=body.encode("utf-8"),
                headers=headers,
                timeout=10,
            )
            return resp.status_code < 300
        except Exception:
            return False
