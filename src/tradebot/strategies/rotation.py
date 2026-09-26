"""Portfolio strategies: cross-sectional momentum rotation and diversified trend.

Evidence: Jegadeesh & Titman (1993) momentum; Liu, Tsyvinski & Wu (2022,
Journal of Finance) "Common Risk Factors in Cryptocurrency" - market, size and
*momentum* factors price the cross-section of crypto returns. A market regime
filter (trade only when BTC is above its moving average) is essential in crypto:
without it momentum portfolios suffered -90% drawdowns in our tests.
"""

from __future__ import annotations

import numpy as np

from ..core import timeframes as tfs
from ..core.candles import CLOSE
from ..strategy.portfolio import PortfolioStrategy
from . import signals


def _find_symbol(symbols: list[str], needle: str) -> str | None:
    needle = needle.lower()
    for s in symbols:
        base = s.split("/")[0].lower()
        if base == needle:
            return s
    return None


class _UniverseMixin:
    """Optional point-in-time universe: vars['universe'] = {symbol: bool array aligned with candles}."""

    def in_universe(self, symbol: str) -> bool:
        uni = self.vars.get("universe")  # type: ignore[attr-defined]
        if not uni:
            return True  # no universe supplied -> every symbol is eligible
        mask = uni.get(symbol)
        if mask is None:
            return False
        i = self._pos.get(symbol, -1)  # type: ignore[attr-defined]
        return bool(i >= 0 and mask[i])


class MomentumRotation(_UniverseMixin, PortfolioStrategy):
    description = "Weekly cross-sectional momentum: hold the top-K coins, only while BTC is in an uptrend."
    timeframe_hint = "1d"
    rebalance_band = 0.03

    def hyperparameters(self):
        return [
            {"name": "lookback_days", "type": int, "min": 7, "max": 90, "default": 28},
            {"name": "top_k", "type": int, "min": 1, "max": 10, "default": 5},
            {"name": "regime_ma_days", "type": int, "min": 0, "max": 200, "default": 100},
            {"name": "rebalance_bars", "type": int, "min": 1, "max": 30, "default": 7},
            {"name": "min_history_days", "type": int, "min": 30, "max": 365, "default": 120},
            {"name": "absolute_momentum", "type": "categorical", "options": [True, False], "default": True},
            {"name": "regime_symbol", "type": "categorical", "options": ["btc"], "default": "btc"},
        ]

    def precompute(self, data):
        n = int(self.hp["regime_ma_days"] * 86_400_000 / tfs.to_ms(self.timeframe))
        out = {}
        reg = _find_symbol(list(data), self.hp["regime_symbol"])
        if reg and n > 0:
            c = data[reg][:, CLOSE]
            out[reg] = {"regime": signals.ma_trend(c, n)}
        return out

    def regime_ok(self) -> bool:
        if int(self.hp["regime_ma_days"]) <= 0:
            return True
        reg = _find_symbol(self.symbols, self.hp["regime_symbol"])
        if reg is None:
            return True
        return self.ind(reg, "regime") > 0

    def should_rebalance(self) -> bool:
        # scheduled (weekly) rebalances, plus an immediate exit/re-entry when the BTC regime flips
        regime = self.regime_ok()
        flipped = self.vars.get("last_regime") is not None and regime != self.vars["last_regime"]
        self.vars["last_regime"] = regime
        return flipped or super().should_rebalance()

    def target_weights(self):
        if not self.regime_ok():
            return {}
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        lb = max(1, round(self.hp["lookback_days"] * bpd))
        min_hist = max(lb + 1, round(self.hp["min_history_days"] * bpd))
        scores = {}
        for s in self.tradable(min_history=min_hist):
            if not self.in_universe(s):
                continue
            c = self.closes(s)
            r = c[-1] / c[-1 - lb] - 1.0
            if np.isfinite(r):
                scores[s] = r
        if self.hp["absolute_momentum"]:
            scores = {s: v for s, v in scores.items() if v > 0}
        top = sorted(scores, key=scores.get, reverse=True)[: int(self.hp["top_k"])]
        if not top:
            return {}
        return {s: 1.0 / len(top) for s in top}


class MultiAssetTrend(_UniverseMixin, PortfolioStrategy):
    description = "Diversified trend following: TS-momentum on every coin, inverse-volatility risk budget."
    timeframe_hint = "1d"
    rebalance_band = 0.02

    def hyperparameters(self):
        return [
            {"name": "base_lookback_days", "type": int, "min": 3, "max": 14, "default": 7},
            {"name": "n_horizons", "type": int, "min": 3, "max": 6, "default": 5},
            {"name": "vol_window_days", "type": int, "min": 10, "max": 90, "default": 30},
            {"name": "risk_budget", "type": float, "min": 0.2, "max": 2.0, "default": 0.8},
            {"name": "max_weight", "type": float, "min": 0.05, "max": 1.0, "default": 0.35},
            {"name": "rebalance_bars", "type": int, "min": 1, "max": 14, "default": 1},
            {"name": "min_history_days", "type": int, "min": 30, "max": 365, "default": 120},
        ]

    def precompute(self, data):
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        lbs = [
            max(1, round(self.hp["base_lookback_days"] * (2**j) * bpd))
            for j in range(int(self.hp["n_horizons"]))
        ]
        ppy = tfs.bars_per_year(self.timeframe)
        win = max(5, round(self.hp["vol_window_days"] * bpd))
        out = {}
        for s, c in data.items():
            close = c[:, CLOSE]
            out[s] = {"sig": signals.tsmom(close, lbs), "vol": signals.realized_vol(close, win, ppy)}
        return out

    def target_weights(self):
        bpd = 86_400_000 / tfs.to_ms(self.timeframe)
        min_hist = round(self.hp["min_history_days"] * bpd)
        universe = [s for s in self.tradable(min_history=min_hist) if self.in_universe(s)]
        if not universe:
            return {}
        n = len(universe)
        w = {}
        for s in universe:
            sig, vol = self.ind(s, "sig"), self.ind(s, "vol")
            if not (np.isfinite(sig) and np.isfinite(vol) and vol > 0) or sig <= 0:
                continue
            w[s] = min(sig * self.hp["risk_budget"] / vol / n, self.hp["max_weight"])
        gross = sum(w.values())
        if gross > 1.0:
            w = {s: v / gross for s, v in w.items()}
        return w
