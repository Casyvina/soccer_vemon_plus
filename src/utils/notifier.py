"""
Push notification senders.

LeagueFluxNotifier (primary): POSTs to LeagueFlux /api/notifications/send,
which fans out to all subscribed browsers via Web Push. This is the preferred
path — notifications appear branded as LeagueFlux and tap-open the app.

NtfyNotifier (fallback): legacy ntfy.sh integration kept for local testing.

Configure via env vars or pass directly:
    LEAGUEFLUX_URL            — LeagueFlux base URL, e.g. https://leagueflux.com
    LEAGUEFLUX_NOTIFY_SECRET  — Bearer token matching NOTIFICATION_API_SECRET on Vercel
    NTFY_URL                  — ntfy topic URL (fallback / testing only)
    NTFY_TOKEN                — ntfy bearer token
"""
from __future__ import annotations

import os

import requests


class LeagueFluxNotifier:
    def __init__(self, base_url: str = "", secret: str = ""):
        base = (base_url or os.getenv("LEAGUEFLUX_URL") or "").rstrip("/")
        self.endpoint = f"{base}/api/notifications/send" if base else ""
        self.secret = secret or os.getenv("LEAGUEFLUX_NOTIFY_SECRET") or ""

    @property
    def configured(self) -> bool:
        return bool(self.endpoint and self.secret)

    def send(self, title: str, body: str, *, url: str = "/app/markets", tag: str = "lf-alert") -> bool:
        if not self.configured:
            return False
        try:
            resp = requests.post(
                self.endpoint,
                json={"title": title, "body": body, "url": url, "tag": tag},
                headers={
                    "Authorization": f"Bearer {self.secret}",
                    "Content-Type": "application/json",
                },
                timeout=10,
            )
            return resp.status_code < 300
        except Exception:
            return False


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
