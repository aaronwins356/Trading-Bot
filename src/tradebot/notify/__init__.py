"""Notifications: Telegram, Discord and generic JSON webhooks.

Sends are fire-and-forget on a background thread with a short timeout so a slow
chat API can never block the trading loop. Failures are logged, never raised.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import NotificationSettings

log = logging.getLogger("tradebot.notify")


@dataclass
class Message:
    kind: str  # trade_open | trade_close | risk | error | jev | info
    title: str
    body: str
    data: dict[str, Any] | None = None

    def text(self) -> str:
        return f"*{self.title}*\n{self.body}" if self.body else f"*{self.title}*"


class Notifier:
    def __init__(self, settings: NotificationSettings | None = None) -> None:
        self.settings = settings or NotificationSettings()
        s = self.settings
        env = os.environ.get
        self.telegram_token = env(s.telegram_token_env) if s.telegram_token_env else None
        self.telegram_chat = env(s.telegram_chat_id_env) if s.telegram_chat_id_env else None
        self.discord_url = env(s.discord_webhook_env) if s.discord_webhook_env else None
        self.webhook_url = env(s.webhook_url_env) if s.webhook_url_env else None
        self._q: queue.Queue[Message | None] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self.sent: list[Message] = []  # kept for tests / the dashboard

    @property
    def enabled(self) -> bool:
        return bool((self.telegram_token and self.telegram_chat) or self.discord_url or self.webhook_url)

    def channels(self) -> list[str]:
        out = []
        if self.telegram_token and self.telegram_chat:
            out.append("telegram")
        if self.discord_url:
            out.append("discord")
        if self.webhook_url:
            out.append("webhook")
        return out

    def send(self, msg: Message) -> None:
        if msg.kind not in self.settings.events and msg.kind != "info":
            return
        self.sent.append(msg)
        del self.sent[:-200]
        if not self.enabled:
            return
        self._ensure_thread()
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            log.warning("notification queue full; dropping %s", msg.title)

    def _ensure_thread(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="notifier", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        with httpx.Client(timeout=10.0) as client:
            while True:
                msg = self._q.get()
                if msg is None:
                    return
                self._deliver(client, msg)

    def _deliver(self, client: httpx.Client, msg: Message) -> None:
        try:
            if self.telegram_token and self.telegram_chat:
                client.post(
                    f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                    json={"chat_id": self.telegram_chat, "text": msg.text(), "parse_mode": "Markdown"},
                )
            if self.discord_url:
                client.post(self.discord_url, json={"content": msg.text().replace("*", "**")[:1900]})
            if self.webhook_url:
                client.post(
                    self.webhook_url,
                    json={"kind": msg.kind, "title": msg.title, "body": msg.body, "data": msg.data or {}},
                )
        except Exception as exc:  # never let notifications break trading
            log.warning("notification failed: %s", exc)

    def close(self) -> None:
        if self._thread and self._thread.is_alive():
            self._q.put(None)
