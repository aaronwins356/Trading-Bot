"""Volatility breakout (Larry Williams) - intraday, flat every night.

Evidence: Williams, "Long-Term Secrets to Short-Term Trading"; widely used by
crypto bot communities. Our research (docs/STRATEGIES.md) finds a *gross* Sharpe
around 1.8 on BTC since 2015 but the edge only survives low trading costs
(<= ~8 bps per side, e.g. futures maker/VIP tiers). Treat as conditional.

Rules (1h bars):
    at 00:00 UTC   : level = today's open + k * (yesterday high - yesterday low)
                     place a buy-stop at `level` (optionally only if open > SMA of daily closes)
    intraday       : the stop fills when price trades through `level`
    at 24:00 UTC   : close the position; cancel an unfilled stop
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..core import timeframes as tfs
from ..core.candles import CLOSE, HIGH, LOW, TS
from ..strategy import utils
from ..strategy.base import Strategy
from . import signals


class VolatilityBreakout(Strategy):
    description = "Intraday volatility breakout: buy-stop at open + k x prior range, exit at day end."
    timeframe_hint = "1h"

    def hyperparameters(self):
        return [
            {"name": "k", "type": float, "min": 0.2, "max": 1.0, "default": 0.5},
            {"name": "trend_filter_days", "type": int, "min": 0, "max": 60, "default": 0},
            {"name": "size", "type": float, "min": 0.1, "max": 3.0, "default": 1.0},
            {"name": "target_vol", "type": float, "min": 0.0, "max": 2.0, "default": 0.6},
        ]

    def precompute(self, candles):
        tf_ms = tfs.to_ms(self.timeframe)
        if tf_ms > 3_600_000 * 6:
            raise ValueError("VolatilityBreakout needs intraday bars (<= 6h)")
        ts = candles[:, TS].astype("int64")
        day = ts // 86_400_000
        is_last = ((ts + tf_ms) % 86_400_000) == 0
        s = pd.DataFrame({"day": day, "h": candles[:, HIGH], "l": candles[:, LOW], "c": candles[:, CLOSE]})
        day_high = s.groupby("day")["h"].cummax().to_numpy()
        day_low = s.groupby("day")["l"].cummin().to_numpy()
        # daily closes known at each day's last bar -> causal SMA on completed days
        n = int(self.hp["trend_filter_days"])
        sma = np.full(len(candles), np.nan)
        if n > 0:
            closes = pd.Series(np.where(is_last, candles[:, CLOSE], np.nan))
            daily = closes.dropna()
            ma = daily.rolling(n, min_periods=n).mean()
            sma = ma.reindex(range(len(candles))).ffill().to_numpy()
        bpd = 86_400_000 / tf_ms
        vol = signals.realized_vol(
            candles[:, CLOSE], max(24, round(30 * bpd)), tfs.bars_per_year(self.timeframe)
        )
        return {
            "day_high": day_high,
            "day_low": day_low,
            "is_last": is_last.astype(float),
            "sma": sma,
            "vol": vol,
        }

    def _end_of_day(self) -> bool:
        return self.ind("is_last") == 1.0

    def should_cancel_entry(self) -> bool:
        return self._end_of_day()

    def should_long(self) -> bool:
        if not self._end_of_day():
            return False
        if int(self.hp["trend_filter_days"]) > 0:
            sma = self.ind("sma")
            if np.isnan(sma) or self.close <= sma:
                return False
        return self.ind("day_high") > self.ind("day_low")

    def go_long(self):
        day_range = self.ind("day_high") - self.ind("day_low")
        level = self.close + float(self.hp["k"]) * day_range  # next day's open ~= this close
        weight = float(self.hp["size"])
        tv, vol = float(self.hp["target_vol"]), self.ind("vol")
        if tv > 0 and vol > 0:  # volatility-targeted sizing, like every other strategy here
            weight = min(weight, tv / vol)
        size = min(weight * self.portfolio_value, self.available_margin * self.leverage)
        qty = utils.size_to_qty(size, level, fee_rate=self.fee_rate)
        self.buy = qty, level

    def update_position(self):
        if self._end_of_day():
            self.liquidate("end of day")

    def signal(self):
        return 1.0 if self.is_long else 0.0
