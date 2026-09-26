"""Time-series momentum with volatility targeting (flagship).

Evidence: Moskowitz, Ooi & Pedersen (2012) "Time Series Momentum"; Hurst, Ooi &
Pedersen (2017) "A Century of Evidence on Trend-Following"; for crypto Liu &
Tsyvinski (2021) find strong TS momentum at 1-4 week horizons. Volatility
targeting (Moreira & Muir 2017; Harvey et al. 2018) cuts drawdowns because
exposure is already reduced when turbulence arrives.

Rules (daily bars by default):
    signal   = mean(sign(return over L)) for L in {7,14,28,56,112,224} days  in [-1, 1]
    exposure = clip(signal, 0, 1) * min(max_leverage, target_vol / realized_vol)
Rebalance with a no-trade band to limit turnover.
"""

from __future__ import annotations

import numpy as np

from ..core import timeframes as tfs
from ..core.candles import CLOSE
from ..strategy.base import Strategy
from . import signals


class TrendVolTarget(Strategy):
    description = "Multi-horizon time-series momentum, volatility-targeted, long-only on spot."
    timeframe_hint = "1d"
    supports_short = True  # shorts only used when long_only=False on futures

    def hyperparameters(self):
        return [
            {"name": "base_lookback_days", "type": int, "min": 3, "max": 14, "default": 7},
            {"name": "n_horizons", "type": int, "min": 3, "max": 7, "default": 6},
            {"name": "target_vol", "type": float, "min": 0.2, "max": 1.0, "default": 0.5},
            {"name": "vol_window_days", "type": int, "min": 10, "max": 90, "default": 30},
            {"name": "max_leverage", "type": float, "min": 0.5, "max": 3.0, "default": 1.0},
            {"name": "band", "type": float, "min": 0.0, "max": 0.3, "default": 0.1},
            {"name": "long_only", "type": "categorical", "options": [True, False], "default": True},
        ]

    def precompute(self, candles):
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        close = candles[:, CLOSE]
        lbs = [
            max(1, round(self.hp["base_lookback_days"] * (2**j) * bpd))
            for j in range(int(self.hp["n_horizons"]))
        ]
        sig = signals.tsmom(close, lbs)
        ppy = tfs.bars_per_year(self.timeframe)
        scale = signals.vol_target_scale(
            close,
            self.hp["target_vol"],
            max(5, round(self.hp["vol_window_days"] * bpd)),
            ppy,
            self.hp["max_leverage"],
        )
        lo = 0.0 if self.hp["long_only"] else -1.0
        target = np.clip(sig, lo, 1.0) * scale
        return {"signal": sig, "scale": scale, "target": target}

    def after(self):
        target = self.ind("target")
        if np.isnan(target):
            return
        self.order_target_exposure(
            float(target), band=float(self.hp["band"]), tag=f"tsmom {self.ind('signal'):+.2f}"
        )

    def signal(self):
        t = self.ind("target")
        return None if np.isnan(t) else float(np.clip(t / max(self.hp["max_leverage"], 1e-9), -1, 1))
