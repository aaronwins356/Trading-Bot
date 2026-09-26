"""Bot runtime for paper, live and replay trading.

One ``BotRunner`` = one bot = one thread. Every closed candle:

    exchange.process_bar  (paper: fill resting orders on the new candle; live: sync fills)
 -> risk.update_equity    (daily-loss pause / max-drawdown kill switch)
 -> strategy decision     (same Strategy / PortfolioStrategy classes as the backtester)
 -> orders via RiskGuardedBroker -> exchange
 -> persist equity, orders, trades, JEV decisions, state; notify; push events to the dashboard

Operator commands (pause / resume / halt / flatten / stop) are queued and executed
on the bot's own thread, so they never race the trading loop.
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

import numpy as np

from .. import strategies as registry
from ..backtest.engine import _funding_crossings, _rebalance
from ..config import BotConfig
from ..core import timeframes as tfs
from ..core.candles import CLOSE, TS
from ..core.jsonutil import sanitize
from ..core.types import ClosedTrade, ExchangeType, Fill, Order, OrderRole, OrderType, Side, TradingMode
from ..data.store import DataStore
from ..execution.simulated import ExecutionConfig, SimulatedExchange, exchange_state, restore_exchange_state
from ..jev.engine import engine_from_settings
from ..jev.strategy import JEVStrategy
from ..notify import Message, Notifier
from ..risk.manager import KillSwitch, RiskGuardedBroker, RiskLimits, RiskManager
from ..risk.protections import build_protections
from ..storage.db import Database
from ..strategy.base import Strategy
from ..strategy.portfolio import PortfolioStrategy
from .feed import ExchangeFeed, ReplayFeed

log = logging.getLogger("tradebot.runner")

EventSink = Callable[[dict[str, Any]], None]


class BotRunner:
    def __init__(
        self,
        cfg: BotConfig,
        db: Database,
        notifier: Notifier | None = None,
        store: DataStore | None = None,
        on_event: EventSink | None = None,
        client_factory: Callable[[Any], Any] | None = None,
        fresh: bool = False,
    ) -> None:
        self.cfg = cfg
        self.db = db
        self.notifier = notifier or Notifier()
        self.store = store or DataStore()
        self.on_event = on_event
        self.client_factory = client_factory
        self.fresh = fresh
        self.status = "stopped"
        self.error = ""
        self.bars_processed = 0
        self.last_bar_ts: int | None = None
        self.started_at: int | None = None
        self._stop = threading.Event()
        self._cmds: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._consecutive_errors = 0
        self._flattened = False
        self.exchange: SimulatedExchange | None = None
        self.broker: RiskGuardedBroker | None = None
        self.risk: RiskManager | None = None
        self.routes: dict[str, Strategy] = {}
        self.portfolio: PortfolioStrategy | None = None
        self.buffers: dict[str, np.ndarray] = {}
        self.feeds: dict[str, Any] = {}
        self.strategy_cls: type = registry.get(cfg.strategy)
        self.tf_ms = tfs.to_ms(cfg.timeframe)

    # ================================================================== control
    @property
    def mode(self) -> TradingMode:
        return TradingMode.LIVE if self.cfg.mode == "live" else TradingMode.PAPER

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.status, self.error = "starting", ""
        self._thread = threading.Thread(target=self._main, name=f"bot-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True, timeout: float = 30.0) -> None:
        self._stop.set()
        self._cmds.put(("noop", None))
        if wait and self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def command(self, name: str, arg: Any = None) -> None:
        """pause | resume | halt | flatten - executed on the bot thread."""
        if name not in ("pause", "resume", "halt", "flatten"):
            raise ValueError(f"unknown command {name}")
        self._cmds.put((name, arg))
        if not (self._thread and self._thread.is_alive()):
            self._process_commands()  # bot not running: apply to persisted state directly

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    # ================================================================== setup
    def _emit(self, event_type: str, **data: Any) -> None:
        if self.on_event:
            with contextlib.suppress(Exception):  # a UI subscriber must never break trading
                self.on_event(
                    {
                        "type": event_type,
                        "bot_id": self.cfg.id,
                        "ts": int(time.time() * 1000),
                        **sanitize(data),
                    }
                )

    def _event(self, level: str, kind: str, message: str, notify: bool = False, **data: Any) -> None:
        ts = self.exchange.now if self.exchange and self.exchange.now else None
        self.db.add_event(self.cfg.id, level, kind, message, data, ts=ts)
        getattr(log, level if level in ("info", "warning", "error") else "info")(
            "[%s] %s", self.cfg.id, message
        )
        self._emit("log", level=level, kind=kind, message=message)
        if notify:
            self.notifier.send(
                Message(
                    kind if kind in ("risk", "error", "jev") else "info",
                    f"{self.cfg.name}: {kind}",
                    message,
                    data,
                )
            )

    def _client(self) -> Any:
        if self.client_factory is not None:
            return self.client_factory(self.cfg.exchange)
        from ..execution.ccxt_exchange import build_ccxt_client

        return build_ccxt_client(self.cfg.exchange)

    def _setup(self) -> None:
        cfg = self.cfg
        etype = ExchangeType.FUTURES if cfg.exchange.market_type == "futures" else ExchangeType.SPOT
        if cfg.mode == "live":
            from ..execution.ccxt_exchange import CCXTExchange

            client = self._client()
            self.exchange = CCXTExchange(
                cfg.exchange,
                cfg.capital,
                cfg.id,
                leverage=cfg.costs.leverage,
                client=client,
                symbols=cfg.symbols,
            )
            self.feeds = {s: ExchangeFeed(client, s, cfg.timeframe) for s in cfg.symbols}
        else:
            self.exchange = SimulatedExchange(
                ExecutionConfig(
                    starting_balance=cfg.capital,
                    fee_maker=cfg.costs.fee_maker,
                    fee_taker=cfg.costs.fee_taker,
                    slippage_bps=cfg.costs.slippage_bps,
                    exchange_type=etype,
                    leverage=cfg.costs.leverage if etype is ExchangeType.FUTURES else 1.0,
                    funding_rate_8h=cfg.costs.funding_rate_8h,
                )
            )
            if cfg.mode == "replay":
                self.feeds = {s: self._replay_feed(s) for s in cfg.symbols}
            else:
                client = self._client()
                self.feeds = {s: ExchangeFeed(client, s, cfg.timeframe) for s in cfg.symbols}
        self.exchange.strategy_name = self.strategy_cls.__name__

        self.risk = RiskManager(
            RiskLimits.from_dict(cfg.risk.model_dump()), build_protections(cfg.risk.protections)
        )
        self.risk.listeners.append(
            lambda ev: self._event("warning", "risk", ev.message, notify=True, **ev.data)
        )
        self.broker = RiskGuardedBroker(
            self.exchange,
            self.risk,
            on_reject=lambda o, why: self._event(
                "warning", "order_rejected", f"{o.side} {o.qty:.8g} {o.symbol} rejected: {why}"
            ),
        )

        saved = self.db.load_state(cfg.id)
        state = None if self.fresh else saved
        if self.fresh and saved and (saved.get("risk") or {}).get("state") in ("halted", "paused"):
            # a fresh start resets the account, never the kill switch: it needs an explicit re-arm
            r = saved["risk"]
            self.risk.state = KillSwitch(r["state"])
            self.risk.state_reason = r.get("reason", "")
            self.risk.daily_pause = bool(r.get("daily_pause", False))
        if state:
            restore_exchange_state(self.exchange, state.get("exchange", {}))
            self.risk.load_state(state.get("risk", {}))
            if cfg.mode == "replay" and state.get("replay_index"):
                for s, f in self.feeds.items():
                    f.i = int(state["replay_index"].get(s, f.i))
            self._event(
                "info",
                "restore",
                f"restored state from {time.strftime('%Y-%m-%d %H:%M', time.gmtime((state.get('saved_at') or 0) / 1000))} UTC",
            )

        warm = max(cfg.warmup_bars, 50)
        for s, f in self.feeds.items():
            self.buffers[s] = f.warmup(warm)
            if len(self.buffers[s]):
                self.exchange.mark(s, float(self.buffers[s][-1, CLOSE]))
        if not any(len(b) for b in self.buffers.values()):
            raise RuntimeError("no warm-up candles available")

        self.exchange.add_order_listener(lambda o: self.db.upsert_order(cfg.id, o))
        self.exchange.add_fill_listener(self._on_fill)

        hp = dict(cfg.hp)
        if issubclass(self.strategy_cls, PortfolioStrategy):
            self.portfolio = self.strategy_cls()
            self.portfolio._bind(self.buffers, cfg.timeframe, hp)
            if state and state.get("vars"):
                self.portfolio.vars.update(state["vars"].get("*", {}))
        else:
            shared: dict[str, Any] = {}
            for s in cfg.symbols:
                st = self.strategy_cls()
                st._bind(
                    symbol=s,
                    timeframe=cfg.timeframe,
                    candles=self.buffers[s],
                    broker=self.broker,  # type: ignore[arg-type]
                    hp=hp,
                    exchange=cfg.exchange.id,
                    mode=self.mode,
                    shared_vars=shared,
                    logger=lambda msg, level, _s=s: self._event(
                        level if level in ("info", "warning", "error") else "info", "strategy", f"{_s}: {msg}"
                    ),
                )
                st._i = len(self.buffers[s]) - 1
                if state and state.get("vars"):
                    st.vars.update(state["vars"].get(s, {}))
                if issubclass(self.strategy_cls, JEVStrategy):
                    allow_short = bool(hp.get("allow_short")) and etype is ExchangeType.FUTURES
                    st.vars["jev_engine"] = engine_from_settings(
                        cfg.jev,
                        journal=self._journal,
                        cache_dir=self.store.root / "jev_cache",
                        allow_short=allow_short,
                    )
                    st.vars["jev_engine"].max_exposure = cfg.jev.max_exposure
                    st.vars["anonymize"] = cfg.jev.anonymize
                self.routes[s] = st

    def _replay_feed(self, symbol: str) -> ReplayFeed:
        rc = self.cfg.replay
        candles = self.store.load(rc.exchange, symbol, self.cfg.timeframe)
        start = 0
        if rc.start:
            from ..data.store import _ms

            start = int(np.searchsorted(candles[:, TS], _ms(rc.start)))
        start = max(start, min(self.cfg.warmup_bars, len(candles) - 1))
        return ReplayFeed(candles, self.cfg.timeframe, start, rc.speed)

    # ================================================================== events
    def _on_fill(
        self, order: Order, fill: Fill, before: float, after: float, closed: ClosedTrade | None
    ) -> None:
        st = self.routes.get(order.symbol)
        if st is not None:
            st._on_fill(order, fill, before, after, closed)
        if closed is not None:
            self.db.add_trade(self.cfg.id, closed)
            assert self.risk is not None
            self.risk.on_trade_closed(closed)
            icon = "WIN" if closed.pnl > 0 else "LOSS"
            self.notifier.send(
                Message(
                    "trade_close",
                    f"{self.cfg.name}: closed {closed.side} {closed.symbol} ({icon})",
                    f"PnL {closed.pnl:+.2f} ({closed.return_pct:+.2f}%) exit {closed.exit_price:.6g} - {closed.exit_reason}",
                    closed.to_dict(),
                )
            )
            self._emit("trade", trade=closed.to_dict())
        if abs(before) < 1e-12 < abs(after):
            side = "LONG" if after > 0 else "SHORT"
            self.notifier.send(
                Message(
                    "trade_open",
                    f"{self.cfg.name}: opened {side} {order.symbol}",
                    f"{abs(after):.6g} @ {fill.price:.6g} ({order.role})",
                )
            )
        self._emit("fill", fill=fill.to_dict())

    def _journal(self, fd: Any, ctx: Any, raw: str) -> None:
        self.db.add_decision(
            self.cfg.id,
            ts=fd.ts,
            symbol=self.cfg.symbols[0] if len(self.cfg.symbols) == 1 else fd.symbol,
            model=fd.model or ("consensus-only" if fd.source == "consensus" else ""),
            authority=fd.authority,
            consensus=fd.consensus,
            llm_exposure=fd.llm.target_exposure if fd.llm else None,
            final_exposure=fd.final_exposure,
            confidence=fd.llm.confidence if fd.llm else None,
            action=fd.action,
            rationale=(fd.llm.rationale if fd.llm else fd.note)[:1000],
            context=ctx.to_dict(),
            raw_response=raw[:8000],
            latency_ms=fd.latency_ms,
            error=fd.error,
        )
        self._emit("decision", final_exposure=fd.final_exposure, consensus=fd.consensus, source=fd.source)
        if fd.llm and fd.source == "llm-veto":
            self.notifier.send(
                Message(
                    "jev",
                    f"{self.cfg.name}: JEV reduced exposure",
                    f"{fd.consensus:.2f} -> {fd.final_exposure:.2f}: {fd.llm.rationale}",
                )
            )

    # ================================================================== commands
    def _process_commands(self) -> None:
        while True:
            try:
                name, arg = self._cmds.get_nowait()
            except queue.Empty:
                return
            if name == "noop" or self.risk is None:
                if name != "noop" and self.risk is None:
                    self._apply_offline(name, arg)
                continue
            if name == "pause":
                self.risk.pause(
                    f"operator pause{': ' + arg if arg else ''}", self.exchange.now if self.exchange else 0
                )
            elif name == "resume":
                self.risk.resume(arg or "operator", self.exchange.now if self.exchange else 0)
                self._flattened = False
            elif name == "halt":
                self.risk.halt(arg or "operator kill switch", self.exchange.now if self.exchange else 0)
                if self.cfg.risk.flatten_on_halt:
                    self.flatten()
            elif name == "flatten":
                self.flatten()
            self._save_state()

    def _apply_offline(self, name: str, arg: Any) -> None:
        state = self.db.load_state(self.cfg.id) or {}
        risk = state.setdefault("risk", {})
        if name == "halt":
            risk.update(state="halted", reason=arg or "operator kill switch")
        elif name == "pause":
            risk.update(state="paused", reason=arg or "operator pause")
        elif name == "resume":
            risk.update(state="running", reason="", peak_equity=0.0, daily_pause=False)
        self.db.save_state(self.cfg.id, state)
        self._event("warning", "risk", f"{name} applied while bot stopped")

    def flatten(self) -> None:
        """Cancel every order and close every position at market (reduce-only passes risk checks)."""
        if self.exchange is None or self.broker is None:
            return
        self.exchange.cancel_all()
        for sym, pos in list(self.exchange.positions.items()):
            if pos.is_open:
                side = Side.SELL if pos.is_long else Side.BUY
                self.broker.submit(
                    Order(
                        sym,
                        side,
                        OrderType.MARKET,
                        abs(pos.qty),
                        role=OrderRole.CLOSE,
                        reduce_only=True,
                        tag="flatten",
                    )
                )
        self._flattened = True
        self._event("warning", "risk", "all positions flattened", notify=True)

    # ================================================================== main loop
    def _main(self) -> None:
        self.started_at = int(time.time() * 1000)
        try:
            self._setup()
            self.status = "running"
            self.db.set_bot_status(self.cfg.id, "running")
            self._event(
                "info",
                "start",
                f"bot started in {self.cfg.mode} mode: {self.cfg.strategy} {self.cfg.symbols} {self.cfg.timeframe}",
            )
            self._emit("status", status=self.status)
            self._loop()
            if self.status == "running":
                self.status = (
                    "finished"
                    if self.cfg.mode == "replay"
                    and all(getattr(f, "done", False) for f in self.feeds.values())
                    else "stopped"
                )
        except Exception as exc:
            self.status, self.error = "error", f"{type(exc).__name__}: {exc}"
            self._event(
                "error",
                "error",
                f"bot crashed: {self.error}",
                notify=True,
                traceback=traceback.format_exc()[-4000:],
            )
        finally:
            try:
                self._save_state()
            except Exception:  # pragma: no cover
                log.exception("failed to save state")
            self.db.set_bot_status(self.cfg.id, self.status, self.error)
            self._emit("status", status=self.status, error=self.error)

    def _loop(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            self._process_commands()
            try:
                new = {s: f.poll() for s, f in self.feeds.items()}
                backoff = 1.0
            except Exception as exc:  # network/exchange hiccup: retry with backoff
                self._event("warning", "feed", f"candle poll failed: {exc}")
                self._stop.wait(min(backoff, 60.0))
                backoff *= 2
                continue
            if not any(len(v) for v in new.values()):
                if self.cfg.mode == "replay" and all(f.done for f in self.feeds.values()):
                    return
                wait = min(f.seconds_until_next_close(self.cfg.candle_delay_s) for f in self.feeds.values())
                self._wait(wait)
                continue
            by_ts: dict[int, dict[str, np.ndarray]] = {}
            for s, arr in new.items():
                for row in arr:
                    by_ts.setdefault(int(row[TS]), {})[s] = row
            for ts in sorted(by_ts):
                if self._stop.is_set():
                    return
                self._on_bar(ts, by_ts[ts])
            if self.cfg.mode == "replay":
                speed = min(f.seconds_until_next_close() for f in self.feeds.values())
                if speed > 0:
                    self._wait(speed)

    def _wait(self, seconds: float) -> None:
        end = time.time() + seconds
        while not self._stop.is_set():
            remaining = end - time.time()
            if remaining <= 0:
                return
            try:
                name, arg = self._cmds.get(timeout=min(remaining, 1.0))
                self._cmds.put((name, arg))
                self._process_commands()
            except queue.Empty:
                pass

    def _on_bar(self, ts: int, bars: dict[str, np.ndarray]) -> None:
        ex, risk = self.exchange, self.risk
        assert ex is not None and risk is not None
        ex.set_time(ts + 1)
        for s, bar in bars.items():
            ex.process_bar(s, bar)
        close_time = ts + self.tf_ms
        prev_close = self.last_bar_ts + self.tf_ms if self.last_bar_ts is not None else None
        ex.set_time(close_time)
        keep = max(self.cfg.warmup_bars, 1000) + 50
        for s, bar in bars.items():
            buf = self.buffers.get(s, np.empty((0, 6)))
            if len(buf) and int(buf[-1, TS]) >= int(bar[TS]):
                continue
            self.buffers[s] = np.vstack([buf, bar])[-keep:]
        if ex.config.funding_rate_8h and prev_close is not None and self.cfg.mode != "live":
            for _ in range(_funding_crossings(prev_close, close_time)):
                for s in bars:
                    ex.apply_funding(s, ex.config.funding_rate_8h)
        risk.update_equity(ex.equity(), close_time)
        if risk.state is KillSwitch.HALTED and self.cfg.risk.flatten_on_halt and not self._flattened:
            self.flatten()
        if self.cfg.mode == "live" and self.bars_processed % 6 == 0 and hasattr(ex, "reconcile"):
            problems = ex.reconcile()
            if problems:
                risk.halt("exchange reconciliation failed: " + "; ".join(problems), close_time)
        try:
            self._decide(bars, close_time)
            self._consecutive_errors = 0
        except Exception as exc:
            self._consecutive_errors += 1
            self._event(
                "error",
                "strategy_error",
                f"strategy error: {exc}",
                notify=True,
                traceback=traceback.format_exc()[-4000:],
            )
            if self._consecutive_errors >= 3:
                raise RuntimeError(
                    "strategy failed 3 bars in a row - stopping (positions and exchange stops are left in place)"
                ) from exc
        self.bars_processed += 1
        self.last_bar_ts = ts
        price = float(next(iter(bars.values()))[CLOSE])
        self.db.add_equity(self.cfg.id, close_time, ex.equity(), ex.balance, ex.gross_exposure(), price)
        self._save_state()
        self._emit("bar", ts=close_time, equity=ex.equity(), price=price, exposure=ex.gross_exposure())

    def _decide(self, bars: dict[str, np.ndarray], close_time: int) -> None:
        if self.portfolio is not None:
            st = self.portfolio
            st._bind(self.buffers, self.cfg.timeframe, st.hp)
            st._pos = {s: len(b) - 1 for s, b in self.buffers.items()}
            st._now = close_time
            eqv = self.exchange.equity()
            st._equity = eqv
            st._weights = (
                {s: p.qty * p.last_price / eqv for s, p in self.exchange.positions.items() if p.is_open}
                if eqv > 0
                else {}
            )
            if st.should_rebalance():
                logs: list[dict[str, Any]] = []
                _rebalance(self.broker, st, st.target_weights() or {}, self.exchange.config, logs)  # type: ignore[arg-type]
                for entry in logs:
                    self._event(entry["level"], "rebalance", entry["msg"])
            st.index += 1
            return
        for s in bars:
            st = self.routes[s]
            st._set_candles(self.buffers[s])
            st._execute(len(self.buffers[s]) - 1)

    def _save_state(self) -> None:
        if self.exchange is None or self.risk is None:
            return
        vars_: dict[str, Any] = {}
        for s, st in self.routes.items():
            vars_[s] = _plain(st.vars)
        if self.portfolio is not None:
            vars_["*"] = _plain(self.portfolio.vars)
        state = {
            "saved_at": int(time.time() * 1000),
            "exchange": exchange_state(self.exchange),
            "risk": self.risk.to_state(),
            "vars": vars_,
            "last_bar_ts": self.last_bar_ts,
            "bars_processed": self.bars_processed,
        }
        if self.cfg.mode == "replay":
            state["replay_index"] = {s: f.i for s, f in self.feeds.items()}
        self.db.save_state(self.cfg.id, sanitize(state))

    # ================================================================== status
    def snapshot(self) -> dict[str, Any]:
        ex = self.exchange
        out: dict[str, Any] = {
            "id": self.cfg.id,
            "name": self.cfg.name,
            "status": self.status,
            "error": self.error,
            "mode": self.cfg.mode,
            "strategy": self.cfg.strategy,
            "symbols": self.cfg.symbols,
            "timeframe": self.cfg.timeframe,
            "bars_processed": self.bars_processed,
            "last_bar_ts": self.last_bar_ts,
            "started_at": self.started_at,
        }
        if ex is not None:
            eq = ex.equity()
            out.update(
                equity=eq,
                balance=ex.balance,
                capital=self.cfg.capital,
                pnl=eq - self.cfg.capital,
                pnl_pct=(eq / self.cfg.capital - 1) * 100 if self.cfg.capital else 0.0,
                exposure=ex.gross_exposure(),
                positions=[p.to_dict() for p in ex.positions.values() if p.is_open],
                open_orders=[o.to_dict() for o in ex.active_orders()],
                fees_paid=ex.total_fees,
            )
        if self.risk is not None:
            out["risk"] = {"state": str(self.risk.state), "reason": self.risk.state_reason}
        feeds = list(self.feeds.values())
        if feeds and isinstance(feeds[0], ReplayFeed):
            out["replay_progress"] = min(f.progress for f in feeds)
        return sanitize(out)


def _is_plain(v: Any, depth: int = 0) -> bool:
    if depth > 6:
        return False
    if v is None or isinstance(v, str | bool | int | float):
        return True
    if isinstance(v, list | tuple):
        return len(v) <= 10_000 and all(_is_plain(x, depth + 1) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _is_plain(x, depth + 1) for k, x in v.items())
    return False


def _plain(d: dict[str, Any]) -> dict[str, Any]:
    """Only JSON-native strategy vars are persisted (engines, arrays and objects are rebuilt on start)."""
    return {k: sanitize(v) for k, v in d.items() if _is_plain(v)}
