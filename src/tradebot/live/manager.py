"""BotManager: owns every BotRunner, persists bot configs, and fans events out to
dashboard WebSocket subscribers."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from ..config import AppSettings, BotConfig
from ..data.store import DataStore
from ..notify import Notifier
from ..storage.db import Database
from .runner import BotRunner

log = logging.getLogger("tradebot.manager")


class BotManager:
    def __init__(
        self,
        settings: AppSettings,
        db: Database | None = None,
        store: DataStore | None = None,
        client_factory: Any = None,
    ) -> None:
        self.settings = settings
        self.db = db or Database(settings.resolved_db_url())
        self.store = store or DataStore(settings.resolved_data_dir())
        self.notifier = Notifier(settings.notifications)
        self.client_factory = client_factory
        self.runners: dict[str, BotRunner] = {}
        self._lock = threading.RLock()
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # bots declared in the YAML config are upserted into the database
        for b in settings.bots:
            self.db.upsert_bot(b.id, b.name, b.model_dump())

    # ------------------------------------------------------------------ events -> websockets
    def attach_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, event: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        for q in list(self._subscribers):
            loop.call_soon_threadsafe(_put_nowait, q, event)

    # ------------------------------------------------------------------ bots
    def configs(self) -> list[BotConfig]:
        out = []
        for row in self.db.list_bots():
            try:
                out.append(BotConfig.model_validate(row.config))
            except Exception as exc:  # corrupt/old config
                log.warning("skipping bot %s: %s", row.id, exc)
        return out

    def get_config(self, bot_id: str) -> BotConfig | None:
        row = self.db.get_bot(bot_id)
        return BotConfig.model_validate(row.config) if row else None

    def save_config(self, cfg: BotConfig) -> BotConfig:
        with self._lock:
            r = self.runners.get(cfg.id)
            if r and r.status in ("running", "starting"):
                raise RuntimeError("stop the bot before editing its configuration")
            self.db.upsert_bot(cfg.id, cfg.name, cfg.model_dump())
            self.runners.pop(cfg.id, None)
            return cfg

    def delete(self, bot_id: str) -> None:
        with self._lock:
            r = self.runners.pop(bot_id, None)
            if r:
                r.stop()
            self.db.delete_bot(bot_id)

    def runner(self, bot_id: str) -> BotRunner:
        with self._lock:
            r = self.runners.get(bot_id)
            if r is None:
                cfg = self.get_config(bot_id)
                if cfg is None:
                    raise KeyError(bot_id)
                r = BotRunner(
                    cfg,
                    self.db,
                    self.notifier,
                    self.store,
                    on_event=self.publish,
                    client_factory=self.client_factory,
                )
                self.runners[bot_id] = r
            return r

    def start(self, bot_id: str, fresh: bool = False) -> BotRunner:
        with self._lock:
            r = self.runner(bot_id)
            if r.status in ("running", "starting"):
                return r
            if fresh or r.status in ("finished", "error", "stopped"):
                cfg = self.get_config(bot_id)
                assert cfg is not None
                r = BotRunner(
                    cfg,
                    self.db,
                    self.notifier,
                    self.store,
                    on_event=self.publish,
                    client_factory=self.client_factory,
                    fresh=fresh,
                )
                self.runners[bot_id] = r
            r.start()
            return r

    def stop(self, bot_id: str) -> None:
        r = self.runners.get(bot_id)
        if r:
            r.stop(wait=True)

    def command(self, bot_id: str, name: str, arg: Any = None) -> None:
        self.runner(bot_id).command(name, arg)

    def kill_all(self, reason: str = "global kill switch", flatten: bool = False) -> int:
        n = 0
        for cfg in self.configs():
            r = self.runner(cfg.id)
            r.command("halt", reason)
            if flatten:
                r.command("flatten")
            n += 1
        return n

    def autostart(self) -> None:
        for cfg in self.configs():
            row = self.db.get_bot(cfg.id)
            if cfg.autostart or (row is not None and row.status == "running"):
                log.info("auto-starting bot %s", cfg.id)
                self.start(cfg.id)

    def shutdown(self) -> None:
        for r in list(self.runners.values()):
            r.stop(wait=True, timeout=10)
        self.notifier.close()

    def status(self, bot_id: str) -> dict[str, Any]:
        r = self.runners.get(bot_id)
        if r is not None and r.exchange is not None:
            return r.snapshot()
        cfg = self.get_config(bot_id)
        row = self.db.get_bot(bot_id)
        state = self.db.load_state(bot_id) or {}
        exs = state.get("exchange", {})
        base = cfg.model_dump() if cfg else {}
        equity = exs.get("balance", cfg.capital if cfg else 0.0)
        for p in (exs.get("positions") or {}).values():
            equity += p["qty"] * (p.get("last_price") or p["entry_price"]) - p["qty"] * p["entry_price"]
        return {
            "id": bot_id,
            "name": base.get("name"),
            "status": row.status if row else "unknown",
            "error": row.status_reason if row else "",
            "mode": base.get("mode"),
            "strategy": base.get("strategy"),
            "symbols": base.get("symbols"),
            "timeframe": base.get("timeframe"),
            "capital": base.get("capital"),
            "equity": equity,
            "pnl": equity - (base.get("capital") or 0.0),
            "pnl_pct": (equity / base["capital"] - 1) * 100 if base.get("capital") else 0.0,
            "positions": [
                {
                    "symbol": s,
                    "qty": p["qty"],
                    "entry_price": p["entry_price"],
                    "last_price": p.get("last_price"),
                }
                for s, p in (exs.get("positions") or {}).items()
            ],
            "risk": {
                "state": (state.get("risk") or {}).get("state", "running"),
                "reason": (state.get("risk") or {}).get("reason", ""),
            },
            "bars_processed": state.get("bars_processed", 0),
            "last_bar_ts": state.get("last_bar_ts"),
        }


def _put_nowait(q: asyncio.Queue, item: Any) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(item)
