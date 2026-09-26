"""Candle feeds for paper/live trading.

* :class:`ExchangeFeed` - polls CCXT ``fetch_ohlcv`` for newly *closed* candles.
* :class:`ReplayFeed` - streams stored history as if it were live (demo mode,
  integration tests, and "paper trading" when no exchange is reachable).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

from ..core import candles as C
from ..core import timeframes as tfs

log = logging.getLogger("tradebot.feed")


class ExchangeFeed:
    def __init__(self, client: Any, symbol: str, timeframe: str) -> None:
        self.client = client
        self.symbol = symbol
        self.timeframe = timeframe
        self.tf_ms = tfs.to_ms(timeframe)
        self.last_ts: int | None = None
        self.done = False

    def _now(self) -> int:
        return int(time.time() * 1000)

    def warmup(self, n: int) -> np.ndarray:
        since = self._now() - (n + 2) * self.tf_ms
        rows: list[list[float]] = []
        cursor = since
        for _ in range(50):  # pagination guard
            batch = self.client.fetch_ohlcv(self.symbol, self.timeframe, since=cursor, limit=1000)
            if not batch:
                break
            rows.extend(batch)
            nxt = int(batch[-1][0]) + self.tf_ms
            if nxt <= cursor or nxt > self._now():
                break
            cursor = nxt
        arr = _dedupe(np.asarray(rows, dtype="float64")) if rows else np.empty((0, 6))
        arr = C.closed_only(arr, self.timeframe, self._now())
        if len(arr):
            self.last_ts = int(arr[-1, C.TS])
        return arr[-n:]

    def poll(self) -> np.ndarray:
        since = (self.last_ts + self.tf_ms) if self.last_ts is not None else self._now() - 3 * self.tf_ms
        batch = self.client.fetch_ohlcv(self.symbol, self.timeframe, since=since, limit=500)
        arr = _dedupe(np.asarray(batch, dtype="float64")) if batch else np.empty((0, 6))
        arr = C.closed_only(arr, self.timeframe, self._now())
        if self.last_ts is not None and len(arr):
            arr = arr[arr[:, C.TS] > self.last_ts]
        if len(arr):
            self.last_ts = int(arr[-1, C.TS])
        return arr

    def seconds_until_next_close(self, delay_s: float = 5.0) -> float:
        now = self._now()
        nxt = (now // self.tf_ms + 1) * self.tf_ms
        return max((nxt - now) / 1000.0 + delay_s, 1.0)


class ReplayFeed:
    """Replays stored candles one at a time. ``speed`` = seconds slept per candle."""

    def __init__(self, candles: np.ndarray, timeframe: str, start_index: int, speed: float = 0.0) -> None:
        self.candles = candles
        self.timeframe = timeframe
        self.i = max(int(start_index), 1)
        self.speed = speed
        self.done = False
        self.last_ts: int | None = None

    def warmup(self, n: int) -> np.ndarray:
        w = self.candles[max(0, self.i - n) : self.i]
        if len(w):
            self.last_ts = int(w[-1, C.TS])
        return w

    def poll(self) -> np.ndarray:
        if self.i >= len(self.candles):
            self.done = True
            return np.empty((0, 6))
        c = self.candles[self.i : self.i + 1]
        self.i += 1
        self.last_ts = int(c[-1, C.TS])
        return c

    def seconds_until_next_close(self, delay_s: float = 0.0) -> float:
        return self.speed

    @property
    def progress(self) -> float:
        return min(self.i / max(len(self.candles), 1), 1.0)


def _dedupe(arr: np.ndarray) -> np.ndarray:
    if not len(arr):
        return arr
    _, idx = np.unique(arr[:, C.TS], return_index=True)
    return arr[np.sort(idx)]
