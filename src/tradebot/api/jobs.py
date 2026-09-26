"""Background jobs (backtests, data downloads) for the API."""

from __future__ import annotations

import logging
import secrets
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field

from .. import strategies as registry
from ..backtest.engine import BacktestConfig
from ..backtest.runner import BacktestSpec
from ..core import candles as C
from ..core import timeframes as tfs
from ..core.jsonutil import dumps, sanitize
from ..data.store import DataStore
from ..storage.db import Database

log = logging.getLogger("tradebot.jobs")


class BacktestRequest(BaseModel):
    strategy: str = "TrendVolTarget"
    exchange: str = "bitstamp"
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USD"])
    timeframe: str = "1d"
    start: str | None = "2018-01-01"
    end: str | None = None
    hp: dict[str, Any] = Field(default_factory=dict)
    capital: float = 10_000.0
    fee_maker: float = 0.001
    fee_taker: float = 0.001
    slippage_bps: float = 5.0
    exchange_type: Literal["spot", "futures"] = "spot"
    leverage: float = 1.0
    funding_rate_8h: float = 0.0
    universe: Literal["all", "top20"] = "top20"  # portfolio strategies on the coinmetrics dataset


def _chart_candles(
    candles: np.ndarray, timeframe: str, start_ms: int | None, max_bars: int = 2500
) -> tuple[list, str]:
    c = candles if start_ms is None else candles[candles[:, C.TS] >= start_ms]
    tf = timeframe
    for coarser in ("4h", "1d", "3d", "1w"):
        if len(c) <= max_bars:
            break
        if tfs.to_ms(coarser) > tfs.to_ms(tf):
            c = C.resample(c, coarser)
            tf = coarser
    return [[int(r[0]), *(round(float(x), 8) for x in r[1:5])] for r in c], tf


class JobManager:
    def __init__(self, db: Database, store: DataStore, max_workers: int = 2) -> None:
        self.db = db
        self.store = store
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self.jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ backtests
    def submit_backtest(self, req: BacktestRequest) -> str:
        registry.get(req.strategy)  # validate early
        bid = "bt-" + secrets.token_hex(4)
        self.db.save_backtest(bid, status="queued", request=req.model_dump(), progress=0.0)
        self.pool.submit(self._run_backtest, bid, req)
        return bid

    def _run_backtest(self, bid: str, req: BacktestRequest) -> None:
        t0 = time.time()
        try:
            self.db.save_backtest(bid, status="running")
            cls = registry.get(req.strategy)
            cfg = BacktestConfig(
                starting_balance=req.capital,
                fee_maker=req.fee_maker,
                fee_taker=req.fee_taker,
                slippage_bps=req.slippage_bps,
                exchange_type=req.exchange_type,
                leverage=req.leverage if req.exchange_type == "futures" else 1.0,
                funding_rate_8h=req.funding_rate_8h,
            )
            vars_: dict[str, Any] = {}
            if registry.is_portfolio(cls):
                data = {}
                for item in self.store.list():
                    if item["exchange"] == "coinmetrics" and item["timeframe"] == req.timeframe:
                        data[item["symbol"]] = self.store.load("coinmetrics", item["symbol"], req.timeframe)
                if not data:
                    raise ValueError(
                        "portfolio strategies need the coinmetrics dataset: run `tradebot data free`"
                    )
                if req.universe == "top20":
                    from ..data.sources import load_market_caps
                    from ..research.pipeline import point_in_time_universe

                    vars_["universe"] = point_in_time_universe(data, load_market_caps(self.store), 20)
                spec = BacktestSpec(
                    cls,
                    data,
                    req.timeframe,
                    cfg,
                    vars=vars_,
                    benchmark_symbol="BTC/USD" if "BTC/USD" in data else None,
                )
            else:
                data = {s: self.store.load(req.exchange, s, req.timeframe) for s in req.symbols}
                spec = BacktestSpec(cls, data, req.timeframe, cfg)

            last = [0.0]

            def progress(p: float) -> None:
                if p - last[0] >= 0.05:
                    last[0] = p
                    self.db.save_backtest(bid, progress=round(p, 3))

            res = spec.run(req.hp or None, req.start, req.end, progress=progress)
            payload = res.to_dict(max_points=1500)
            if not spec.is_portfolio:
                sym = req.symbols[0]
                from ..data.store import _ms

                payload["candles"], payload["candles_timeframe"] = _chart_candles(
                    data[sym], req.timeframe, _ms(req.start)
                )
                payload["markers"] = [
                    {"time": t.opened_at, "price": t.entry_price, "kind": "entry", "side": t.side}
                    for t in res.trades[-400:]
                ] + [
                    {"time": t.closed_at, "price": t.exit_price, "kind": "exit", "side": t.side, "pnl": t.pnl}
                    for t in res.trades[-400:]
                ]
            m = res.metrics
            summary = {
                k: m.get(k)
                for k in (
                    "cagr_pct",
                    "sharpe",
                    "max_drawdown_pct",
                    "total_trades",
                    "win_rate",
                    "total_return_pct",
                )
            }
            summary["runtime_sec"] = round(time.time() - t0, 2)
            self.db.save_backtest(
                bid, status="done", progress=1.0, summary=sanitize(summary), result_json=dumps(payload)
            )
        except Exception as exc:
            log.exception("backtest %s failed", bid)
            self.db.save_backtest(
                bid, status="error", error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-2000:]}"
            )

    # ------------------------------------------------------------------ generic jobs (downloads)
    def submit(self, kind: str, fn: Any, **kw: Any) -> str:
        jid = f"{kind}-" + secrets.token_hex(4)
        with self._lock:
            self.jobs[jid] = {
                "id": jid,
                "kind": kind,
                "status": "running",
                "started_at": int(time.time() * 1000),
                "result": None,
                "error": "",
            }

        def run() -> None:
            try:
                result = fn(**kw)
                self.jobs[jid].update(status="done", result=sanitize(result))
            except Exception as exc:
                self.jobs[jid].update(status="error", error=f"{type(exc).__name__}: {exc}")

        self.pool.submit(run)
        return jid

    def get(self, jid: str) -> dict[str, Any] | None:
        return self.jobs.get(jid)

    def shutdown(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
