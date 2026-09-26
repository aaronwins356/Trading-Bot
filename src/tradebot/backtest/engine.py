"""Event-driven backtest engine.

Per bar ``t`` (timestamps are candle *open* times):

1. ``process_bar``: resting orders placed at the previous close are matched against
   bar ``t``'s price path (or its 1-minute sub-candles when provided).
2. Funding is charged for every 8h funding timestamp crossed (futures).
3. At the bar close, every strategy runs ``before -> _check -> after``; market orders
   fill immediately at the close +/- slippage.
4. Equity (balance + unrealized PnL) is recorded at the close.

A decision at the close of bar ``t`` can therefore only be filled with prices
from bar ``t`` close onwards - no look-ahead is possible by construction.
"""

from __future__ import annotations

import logging
import math
import time as _time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..core import candles as cndl
from ..core import timeframes as tfs
from ..core.candles import CLOSE, TS
from ..core.types import ClosedTrade, ExchangeType, Fill, Order, OrderRole, OrderType, Side, TradingMode
from ..execution.simulated import ExecutionConfig, SimulatedExchange
from ..strategy.base import Strategy
from ..strategy.portfolio import PortfolioStrategy
from .metrics import compute_metrics

log = logging.getLogger("tradebot.backtest")

FUNDING_INTERVAL_MS = 8 * 3_600_000


def _to_ms(value: str | int | pd.Timestamp | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int | np.integer):
        return int(value)
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return int(ts.value // 1_000_000)


@dataclass
class BacktestResult:
    strategy: str
    symbols: list[str]
    timeframe: str
    hp: dict[str, Any]
    config: dict[str, Any]
    equity: pd.Series
    exposure: pd.Series
    trades: list[ClosedTrade]
    orders: list[Order]
    fills: list[Fill]
    metrics: dict[str, Any]
    benchmark: pd.Series | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    runtime_sec: float = 0.0
    chart_lines: dict[str, list[tuple[int, float]]] = field(default_factory=dict)

    def to_dict(self, max_points: int = 1500, include_orders: bool = False) -> dict[str, Any]:
        """JSON-friendly payload for the API/dashboard (equity down-sampled)."""
        eq = self.equity
        step = max(1, math.ceil(len(eq) / max_points))
        eq_s = eq.iloc[::step]
        if len(eq) and eq_s.index[-1] != eq.index[-1]:
            eq_s = pd.concat([eq_s, eq.iloc[-1:]])
        dd = eq_s / eq_s.cummax() - 1.0
        bench = None
        if self.benchmark is not None and len(self.benchmark):
            b = self.benchmark.reindex(eq_s.index, method="ffill")
            bench = [[int(t.value // 1_000_000), round(float(v), 2)] for t, v in b.items() if not np.isnan(v)]
        payload = {
            "strategy": self.strategy,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "hp": self.hp,
            "config": self.config,
            "metrics": self.metrics,
            "equity": [[int(t.value // 1_000_000), round(float(v), 2)] for t, v in eq_s.items()],
            "drawdown": [[int(t.value // 1_000_000), round(float(v) * 100, 3)] for t, v in dd.items()],
            "benchmark": bench,
            "trades": [t.to_dict() for t in self.trades[-2000:]],
            "logs": self.logs[-500:],
            "runtime_sec": round(self.runtime_sec, 3),
        }
        if include_orders:
            payload["orders"] = [o.to_dict() for o in self.orders[-5000:]]
        return payload


@dataclass
class BacktestConfig(ExecutionConfig):
    close_at_end: bool = True  # flatten open positions at the last bar so every trade is counted


class _Timeline:
    """Union of candle timestamps across symbols with per-symbol cursors."""

    def __init__(self, data: Mapping[str, np.ndarray], start_ms: int | None, end_ms: int | None) -> None:
        all_ts = np.unique(np.concatenate([d[:, TS].astype("int64") for d in data.values() if len(d)]))
        if start_ms is not None:
            all_ts = all_ts[all_ts >= start_ms]
        if end_ms is not None:
            all_ts = all_ts[all_ts < end_ms]
        self.ts = all_ts
        # index of each symbol's candle at each timeline step (-1 if none)
        self.idx: dict[str, np.ndarray] = {}
        for s, d in data.items():
            sts = d[:, TS].astype("int64")
            pos = np.searchsorted(sts, all_ts)
            pos_c = np.clip(pos, 0, max(len(sts) - 1, 0))
            hit = (pos < len(sts)) & (sts[pos_c] == all_ts) if len(sts) else np.zeros(len(all_ts), bool)
            self.idx[s] = np.where(hit, pos, -1)


def _funding_crossings(prev_ms: int, now_ms: int) -> int:
    """Number of 00:00/08:00/16:00 UTC funding timestamps in (prev_ms, now_ms]."""
    return int(now_ms // FUNDING_INTERVAL_MS - prev_ms // FUNDING_INTERVAL_MS)


def run_backtest(
    strategy: type[Strategy] | Strategy,
    data: Mapping[str, np.ndarray] | np.ndarray,
    timeframe: str,
    *,
    symbol: str = "BTC/USDT",
    config: BacktestConfig | None = None,
    hp: dict[str, Any] | None = None,
    start: str | int | None = None,
    end: str | int | None = None,
    sub_candles: Mapping[str, np.ndarray] | None = None,
    n_trials: int = 1,
    compute_stats: bool = True,
    progress: Callable[[float], None] | None = None,
) -> BacktestResult:
    """Backtest a Jesse-style strategy on one or more symbols ("routes")."""
    t0 = _time.perf_counter()
    cfg = config or BacktestConfig()
    if isinstance(data, np.ndarray):
        data = {symbol: data}
    data = {s: np.asarray(d, dtype="float64") for s, d in data.items()}
    ex = SimulatedExchange(cfg)
    shared: dict[str, Any] = {}
    logs: list[dict[str, Any]] = []
    strategies: dict[str, Strategy] = {}
    for s, candles in data.items():
        st = strategy() if isinstance(strategy, type) else strategy
        if not isinstance(strategy, type) and len(data) > 1:
            raise ValueError("pass a Strategy class (not an instance) for multi-symbol backtests")

        def _logger(msg: str, level: str, _s: str = s) -> None:
            logs.append({"ts": ex.now, "symbol": _s, "level": level, "msg": msg})

        def _provider(sym: str, tf: str, _now=lambda: ex.now) -> np.ndarray:
            base = data[sym]
            hi = int(np.searchsorted(base[:, TS], _now() - tfs.to_ms(timeframe) + 1, side="left"))
            window = base[:hi]
            if tf == timeframe:
                return window
            return cndl.closed_only(cndl.resample(window, tf), tf, _now())

        st._bind(
            symbol=s,
            timeframe=timeframe,
            candles=candles,
            broker=ex,
            hp=hp,
            mode=TradingMode.BACKTEST,
            shared_vars=shared,
            candle_provider=_provider,
            logger=_logger,
        )
        strategies[s] = st
    ex.strategy_name = next(iter(strategies.values())).name

    def _dispatch(order: Order, fill: Fill, before: float, after: float, closed: ClosedTrade | None) -> None:
        st = strategies.get(order.symbol)
        if st is not None:
            st._on_fill(order, fill, before, after, closed)

    ex.add_fill_listener(_dispatch)

    start_ms, end_ms = _to_ms(start), _to_ms(end)
    tl = _Timeline(data, start_ms, end_ms)
    tf_ms = tfs.to_ms(timeframe)
    sub_bounds: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    if sub_candles:
        for s, sub in sub_candles.items():
            if s in data:
                sub = np.asarray(sub, dtype="float64")
                bars_ts = data[s][:, TS]
                lo = np.searchsorted(sub[:, TS], bars_ts, side="left")
                hi = np.searchsorted(sub[:, TS], bars_ts + tf_ms, side="left")
                sub_bounds[s] = (sub, lo, hi)

    # warm start: mark prices at the bar before the first traded bar
    for s, d in data.items():
        first = tl.idx[s][tl.idx[s] >= 0]
        if first.size and first[0] > 0:
            ex.mark(s, d[first[0] - 1, CLOSE])

    n = len(tl.ts)
    eq_ts = np.empty(n, dtype="int64")
    eq_val = np.empty(n)
    eq_expo = np.empty(n)
    prev_close_time: int | None = None
    funding = cfg.funding_rate_8h if cfg.exchange_type is ExchangeType.FUTURES else 0.0
    report_every = max(n // 100, 1)
    for k in range(n):
        t = int(tl.ts[k])
        ex.set_time(t + 1)  # intrabar events are timestamped strictly inside the bar
        active_syms = []
        for s in strategies:
            i = int(tl.idx[s][k])
            if i < 0:
                continue
            active_syms.append((s, i))
            bar = data[s][i]
            sub = None
            if s in sub_bounds and ex.has_active_orders(s):
                arr, lo, hi = sub_bounds[s]
                sub = arr[lo[i] : hi[i]]
            ex.process_bar(s, bar, sub)
        close_time = t + tf_ms
        ex.set_time(close_time)
        if funding and prev_close_time is not None:
            crossings = _funding_crossings(prev_close_time, close_time)
            for _ in range(crossings):
                for s in data:
                    ex.apply_funding(s, funding)
        prev_close_time = close_time
        for s, i in active_syms:
            strategies[s]._execute(i)
        eq_ts[k] = close_time
        eq_val[k] = ex.equity()
        eq_expo[k] = ex.gross_exposure()
        if progress is not None and k % report_every == 0:
            progress(k / n)
        if ex.liquidated:
            logs.append({"ts": close_time, "symbol": "*", "level": "error", "msg": "account liquidated"})
            eq_ts, eq_val, eq_expo = eq_ts[: k + 1], eq_val[: k + 1], eq_expo[: k + 1]
            break

    for st in strategies.values():
        st.terminate()
    if cfg.close_at_end and len(eq_val):
        for s in data:
            pos = ex.position(s)
            if pos.is_open:
                ex.cancel_all(s)
                side = Side.SELL if pos.is_long else Side.BUY
                ex.submit(
                    Order(
                        s,
                        side,
                        OrderType.MARKET,
                        abs(pos.qty),
                        role=OrderRole.CLOSE,
                        reduce_only=True,
                        tag="end of backtest",
                    )
                )
        eq_val[-1] = ex.equity()
        eq_expo[-1] = 0.0

    return _finalize(
        name=next(iter(strategies.values())).name,
        symbols=list(data),
        timeframe=timeframe,
        hp=next(iter(strategies.values())).hp,
        cfg=cfg,
        ex=ex,
        eq_ts=eq_ts,
        eq_val=eq_val,
        eq_expo=eq_expo,
        bench_candles=data[next(iter(data))],
        logs=logs,
        n_trials=n_trials,
        compute_stats=compute_stats,
        t0=t0,
        chart_lines=next(iter(strategies.values())).chart_lines,
    )


def run_portfolio_backtest(
    strategy: type[PortfolioStrategy] | PortfolioStrategy,
    data: Mapping[str, np.ndarray],
    timeframe: str,
    *,
    config: BacktestConfig | None = None,
    hp: dict[str, Any] | None = None,
    start: str | int | None = None,
    end: str | int | None = None,
    benchmark_symbol: str | None = None,
    n_trials: int = 1,
    compute_stats: bool = True,
    progress: Callable[[float], None] | None = None,
) -> BacktestResult:
    """Backtest a target-weight portfolio strategy across many symbols."""
    t0 = _time.perf_counter()
    cfg = config or BacktestConfig()
    data = {s: np.asarray(d, dtype="float64") for s, d in data.items() if len(d)}
    st = strategy() if isinstance(strategy, type) else strategy
    st._bind(data, timeframe, hp)
    ex = SimulatedExchange(cfg)
    ex.strategy_name = st.name
    logs: list[dict[str, Any]] = []
    start_ms, end_ms = _to_ms(start), _to_ms(end)
    tl_all = _Timeline(data, None, end_ms)
    tf_ms = tfs.to_ms(timeframe)
    last_ts = {s: int(d[-1, TS]) for s, d in data.items()}
    n = len(tl_all.ts)
    eq_ts: list[int] = []
    eq_val: list[float] = []
    eq_expo: list[float] = []
    funding = cfg.funding_rate_8h if cfg.exchange_type is ExchangeType.FUTURES else 0.0
    prev_close_time: int | None = None
    started = False
    report_every = max(n // 100, 1)
    for k in range(n):
        t = int(tl_all.ts[k])
        close_time = t + tf_ms
        for s in data:
            i = int(tl_all.idx[s][k])
            if i >= 0:
                st._pos[s] = i
                ex.mark(s, data[s][i, CLOSE])
        ex.set_time(close_time)
        st._now = close_time
        # delisted / data ended: exit at the last known price
        for s, lt in last_ts.items():
            pos = ex.position(s)
            if pos.is_open and t > lt:
                side = Side.SELL if pos.is_long else Side.BUY
                ex.submit(
                    Order(
                        s,
                        side,
                        OrderType.MARKET,
                        abs(pos.qty),
                        role=OrderRole.CLOSE,
                        reduce_only=True,
                        tag="data ended/delisted",
                    )
                )
        if funding and prev_close_time is not None:
            for _ in range(_funding_crossings(prev_close_time, close_time)):
                for s in data:
                    ex.apply_funding(s, funding)
        prev_close_time = close_time
        if start_ms is not None and t < start_ms:
            continue
        started = True
        eqv = ex.equity()
        st._equity = eqv
        st._weights = (
            {s: p.qty * p.last_price / eqv for s, p in ex.positions.items() if p.is_open} if eqv > 0 else {}
        )
        if st.should_rebalance():
            try:
                targets = st.target_weights() or {}
            except Exception as exc:  # strategy bug -> log and hold
                logs.append(
                    {
                        "ts": close_time,
                        "symbol": "*",
                        "level": "error",
                        "msg": f"target_weights failed: {exc!r}",
                    }
                )
                targets = None
            if targets is not None:
                _rebalance(ex, st, targets, cfg, logs)
        st.index += 1
        eq_ts.append(close_time)
        eq_val.append(ex.equity())
        eq_expo.append(ex.gross_exposure())
        if progress is not None and k % report_every == 0:
            progress(k / n)
    if not started:
        raise ValueError("no data inside the requested backtest window")
    if cfg.close_at_end:
        for s, pos in list(ex.positions.items()):
            if pos.is_open:
                side = Side.SELL if pos.is_long else Side.BUY
                ex.submit(
                    Order(
                        s,
                        side,
                        OrderType.MARKET,
                        abs(pos.qty),
                        role=OrderRole.CLOSE,
                        reduce_only=True,
                        tag="end of backtest",
                    )
                )
        eq_val[-1] = ex.equity()
        eq_expo[-1] = 0.0
    bench_sym = benchmark_symbol or next(iter(data))
    return _finalize(
        name=st.name,
        symbols=list(data),
        timeframe=timeframe,
        hp=st.hp,
        cfg=cfg,
        ex=ex,
        eq_ts=np.asarray(eq_ts, dtype="int64"),
        eq_val=np.asarray(eq_val),
        eq_expo=np.asarray(eq_expo),
        bench_candles=data.get(bench_sym),
        logs=logs,
        n_trials=n_trials,
        compute_stats=compute_stats,
        t0=t0,
    )


def _rebalance(
    ex: SimulatedExchange,
    st: PortfolioStrategy,
    targets: dict[str, float],
    cfg: BacktestConfig,
    logs: list[dict[str, Any]],
) -> None:
    eqv = ex.equity()
    if eqv <= 0:
        return
    spot = cfg.exchange_type is ExchangeType.SPOT
    clean: dict[str, float] = {}
    for s, w in targets.items():
        if s not in st._data or not math.isfinite(w):
            continue
        if not st.is_available(s):
            continue  # cannot trade a symbol that has no current candle
        clean[s] = max(w, 0.0) if (spot or not st.supports_short) else w
    gross = sum(abs(w) for w in clean.values())
    cap = cfg.leverage * (1.0 - 4.0 * cfg.fee_taker - 2.0 * cfg.slippage_bps / 10_000.0)
    if gross > cap > 0:
        clean = {s: w * cap / gross for s, w in clean.items()}
    orders: list[Order] = []
    symbols = set(clean) | {s for s, p in ex.positions.items() if p.is_open}
    for s in symbols:
        price = ex.last_price.get(s)
        if not price:
            continue
        if s not in clean and not st.is_available(s):
            continue  # hold positions in symbols without a fresh candle
        target_qty = clean.get(s, 0.0) * eqv / price
        pos = ex.position(s)
        delta = target_qty - pos.qty
        full_exit = abs(target_qty) < 1e-12 and pos.is_open
        if not full_exit and abs(delta) * price < st.rebalance_band * eqv:
            continue
        if abs(delta) * price < cfg.min_notional and not full_exit:
            continue
        side = Side.BUY if delta > 0 else Side.SELL
        reduces = pos.is_open and (np.sign(pos.qty) != side.sign)
        qty = abs(delta)
        if full_exit:
            qty = abs(pos.qty)
        orders.append(
            Order(
                s,
                side,
                OrderType.MARKET,
                qty,
                role=OrderRole.REBALANCE,
                reduce_only=bool(reduces and qty <= abs(pos.qty) + 1e-12),
            )
        )
    # free up margin first: execute reducing orders before increasing ones
    orders.sort(key=lambda o: 0 if o.reduce_only else 1)
    for o in orders:
        res = ex.submit(o)
        if res.status.value == "rejected":
            logs.append(
                {
                    "ts": ex.now,
                    "symbol": o.symbol,
                    "level": "warning",
                    "msg": f"rebalance order rejected: {res.reject_reason}",
                }
            )


def _finalize(
    *,
    name: str,
    symbols: list[str],
    timeframe: str,
    hp: dict[str, Any],
    cfg: BacktestConfig,
    ex: SimulatedExchange,
    eq_ts: np.ndarray,
    eq_val: np.ndarray,
    eq_expo: np.ndarray,
    bench_candles: np.ndarray | None,
    logs: list[dict[str, Any]],
    n_trials: int,
    compute_stats: bool,
    t0: float,
    chart_lines: dict[str, list[tuple[int, float]]] | None = None,
) -> BacktestResult:
    if len(eq_ts):
        # anchor the curve at the starting balance one bar before the first close,
        # so the first bar's costs (entry fees/slippage) are part of the return series
        eq_ts = np.concatenate(([int(eq_ts[0]) - tfs.to_ms(timeframe)], eq_ts))
        eq_val = np.concatenate(([cfg.starting_balance], eq_val))
        eq_expo = np.concatenate(([0.0], eq_expo))
    idx = pd.to_datetime(eq_ts, unit="ms", utc=True)
    equity = pd.Series(eq_val, index=idx, name="equity")
    exposure = pd.Series(eq_expo, index=idx, name="exposure")
    bench = None
    if bench_candles is not None and len(bench_candles) and len(equity):
        b_idx = pd.to_datetime(
            bench_candles[:, TS].astype("int64") + tfs.to_ms(timeframe), unit="ms", utc=True
        )
        b = pd.Series(bench_candles[:, CLOSE], index=b_idx)
        b = b[(b.index >= equity.index[0]) & (b.index <= equity.index[-1])]
        if len(b):
            bench = b / b.iloc[0] * float(equity.iloc[0])
    cfg_dict = {
        k: (str(v) if not isinstance(v, int | float | bool | type(None)) else v)
        for k, v in asdict(cfg).items()
    }
    metrics: dict[str, Any] = {}
    if compute_stats and len(equity) > 2:
        metrics = compute_metrics(
            equity,
            ex.closed_trades,
            exposure=exposure,
            benchmark=bench,
            fees_paid=ex.total_fees,
            funding_paid=ex.total_funding,
            n_trials=n_trials,
        )
    return BacktestResult(
        strategy=name,
        symbols=symbols,
        timeframe=timeframe,
        hp=dict(hp),
        config=cfg_dict,
        equity=equity,
        exposure=exposure,
        trades=list(ex.closed_trades),
        orders=list(ex.orders.values()),
        fills=list(ex.fills),
        metrics=metrics,
        benchmark=bench,
        logs=logs,
        runtime_sec=_time.perf_counter() - t0,
        chart_lines=chart_lines or {},
    )
