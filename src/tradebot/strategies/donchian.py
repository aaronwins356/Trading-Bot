"""Donchian channel breakout ("Turtle" system) with a trailing ATR stop.

Evidence: Richard Dennis' Turtle rules; Clenow (2013) "Following the Trend";
Concretum Group (2025) "Catching Crypto Trends" (Donchian ensembles on BTC/alts).
Written with the full Jesse-style API: should_long / go_long / update_position,
stop-loss modifications (trailing) and liquidate().

Rules (default: 4h bars):
    entry : close > highest high of the previous `entry` bars
    exit  : close < lowest low of the previous `exit` bars  (or trailing ATR stop)
    size  : volatility-targeted exposure, capped at max_leverage
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ta
from ..core import timeframes as tfs
from ..core.candles import CLOSE, HIGH, LOW
from ..strategy import utils
from ..strategy.base import Strategy
from . import signals


class DonchianBreakout(Strategy):
    description = "Turtle-style channel breakout with trailing ATR stop and vol-targeted sizing."
    timeframe_hint = "4h"
    supports_short = True

    def hyperparameters(self):
        return [
            {"name": "entry", "type": int, "min": 10, "max": 200, "default": 55},
            {"name": "exit", "type": int, "min": 5, "max": 100, "default": 20},
            {"name": "atr_period", "type": int, "min": 7, "max": 50, "default": 20},
            {"name": "atr_stop", "type": float, "min": 0.0, "max": 6.0, "default": 3.0},
            {"name": "target_vol", "type": float, "min": 0.2, "max": 1.5, "default": 0.6},
            {"name": "max_leverage", "type": float, "min": 0.5, "max": 3.0, "default": 1.0},
            {"name": "allow_short", "type": "categorical", "options": [False, True], "default": False},
        ]

    def precompute(self, candles):
        e, x = int(self.hp["entry"]), int(self.hp["exit"])
        h, lo = candles[:, HIGH], candles[:, LOW]
        hh = pd.Series(h).rolling(e, min_periods=e).max().shift(1).to_numpy()
        ll = pd.Series(lo).rolling(e, min_periods=e).min().shift(1).to_numpy()
        exit_low = pd.Series(lo).rolling(x, min_periods=x).min().shift(1).to_numpy()
        exit_high = pd.Series(h).rolling(x, min_periods=x).max().shift(1).to_numpy()
        ppy = tfs.bars_per_year(self.timeframe)
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        vol = signals.realized_vol(candles[:, CLOSE], max(10, round(30 * bpd)), ppy)
        state = signals.donchian_state(candles, e, x, allow_short=bool(self.hp["allow_short"]))
        return {
            "hh": hh,
            "ll": ll,
            "exit_low": exit_low,
            "exit_high": exit_high,
            "atr": ta.atr(candles, int(self.hp["atr_period"]), sequential=True),
            "vol": vol,
            "state": state,
        }

    # ------------------------------------------------------------ entries
    def should_long(self) -> bool:
        hh = self.ind("hh")
        return not np.isnan(hh) and self.close > hh

    def should_short(self) -> bool:
        if not self.hp["allow_short"] or self.is_spot_trading:
            return False
        ll = self.ind("ll")
        return not np.isnan(ll) and self.close < ll

    def _qty(self) -> float:
        vol = self.ind("vol")
        lev = float(self.hp["max_leverage"])
        weight = lev if not (vol > 0) else min(lev, float(self.hp["target_vol"]) / vol)
        size = min(weight * self.portfolio_value, self.available_margin * self.leverage)
        return utils.size_to_qty(size, self.price, fee_rate=self.fee_rate)

    def go_long(self):
        qty = self._qty()
        self.buy = qty, self.price
        if self.hp["atr_stop"] > 0 and self.ind("atr") > 0:
            self.stop_loss = qty, self.price - self.hp["atr_stop"] * self.ind("atr")

    def go_short(self):
        qty = self._qty()
        self.sell = qty, self.price
        if self.hp["atr_stop"] > 0 and self.ind("atr") > 0:
            self.stop_loss = qty, self.price + self.hp["atr_stop"] * self.ind("atr")

    # ------------------------------------------------------------ management
    def update_position(self):
        if self.is_long:
            if self.close < self.ind("exit_low"):
                self.liquidate("channel exit")
                return
            if self.hp["atr_stop"] > 0 and self.stop_loss is not None:
                trail = self.close - self.hp["atr_stop"] * self.ind("atr")
                if trail > self.average_stop_loss:
                    self.stop_loss = abs(self.position.qty), trail
        elif self.is_short:
            if self.close > self.ind("exit_high"):
                self.liquidate("channel exit")
                return
            if self.hp["atr_stop"] > 0 and self.stop_loss is not None:
                trail = self.close + self.hp["atr_stop"] * self.ind("atr")
                if trail < self.average_stop_loss:
                    self.stop_loss = abs(self.position.qty), trail

    def signal(self):
        s = self.ind("state")
        return None if np.isnan(s) else float(s)
