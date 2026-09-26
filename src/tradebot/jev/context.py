"""The quant analyst panel and the market context JEV reasons over.

Analysts are the *validated* signal models from :mod:`tradebot.strategies.signals`
(time-series momentum, Donchian breakout, moving-average trend, 20-day breakout).
Their weighted, volatility-targeted average is the **quant consensus** - a
backtestable baseline the LLM can confirm, scale down, or (in ``full`` authority)
override within hard limits.

The context is **anonymised** by default: no ticker, no calendar dates, no
absolute price levels - only relative features. LLMs have memorised a lot of
market history; hiding identity reduces (but cannot eliminate) look-ahead
contamination when replaying the past. Forward paper-trading is the only clean
test of the LLM layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .. import indicators as ta
from ..core import timeframes as tfs
from ..core.candles import CLOSE, HIGH, LOW, TS, VOLUME
from ..strategies import signals

ANALYSTS: dict[str, dict[str, Any]] = {
    "tsmom": {
        "weight": 0.4,
        "description": "Time-series momentum: average sign of returns over 1-32 week horizons.",
        "evidence": "Walk-forward OOS Sharpe ~1.1 on BTC since 2018; recommended.",
    },
    "donchian": {
        "weight": 0.3,
        "description": "Turtle breakout state: long after a 20-day high, flat after a 10-day low.",
        "evidence": "Recommended on 4h bars; robust across parameter grid.",
    },
    "ma_trend": {
        "weight": 0.2,
        "description": "Price above (+1) or below (-1) its 100-day moving average.",
        "evidence": "Classic trend filter; Sharpe ~1.2 on BTC daily in research.",
    },
    "breakout_20d": {
        "weight": 0.1,
        "description": "+1 when price closes at a 20-day high, -1 at a 20-day low, else 0.",
        "evidence": "Quantpedia 'MAX' effect: BTC trends from local maxima.",
    },
}


def analyst_signals(candles: np.ndarray, timeframe: str) -> dict[str, np.ndarray]:
    """Vectorized, causal analyst signals in [-1, 1] aligned with ``candles``."""
    bpd = 86_400_000 / tfs.to_ms(timeframe)
    c = candles[:, CLOSE]
    d = lambda days: max(1, round(days * bpd))  # noqa: E731
    out = {
        "tsmom": signals.tsmom(c, [d(7 * 2**j) for j in range(6)]),
        "donchian": signals.donchian_state(candles, d(20), d(10)),
        "ma_trend": signals.ma_trend(c, d(100)),
    }
    hh = pd.Series(candles[:, HIGH]).rolling(d(20), min_periods=d(20)).max().to_numpy()
    ll = pd.Series(candles[:, LOW]).rolling(d(20), min_periods=d(20)).min().to_numpy()
    b = np.where(c >= hh * 0.999, 1.0, np.where(c <= ll * 1.001, -1.0, 0.0))
    b[np.isnan(hh)] = np.nan
    out["breakout_20d"] = b
    return out


def feature_arrays(candles: np.ndarray, timeframe: str) -> dict[str, np.ndarray]:
    """Relative (scale-free) market features for the LLM context, all causal."""
    bpd = 86_400_000 / tfs.to_ms(timeframe)
    ppy = tfs.bars_per_year(timeframe)
    c = candles[:, CLOSE]
    d = lambda days: max(1, round(days * bpd))  # noqa: E731
    s = pd.Series(c)
    feats: dict[str, np.ndarray] = {}
    for name, days in (("ret_7d_pct", 7), ("ret_30d_pct", 30), ("ret_90d_pct", 90), ("ret_365d_pct", 365)):
        feats[name] = (s / s.shift(d(days)) - 1).to_numpy() * 100
    feats["ret_1bar_pct"] = (s / s.shift(1) - 1).to_numpy() * 100
    for n in (50, 200):
        ma = s.rolling(d(n), min_periods=d(n)).mean()
        feats[f"dist_sma{n}d_pct"] = ((s / ma) - 1).to_numpy() * 100
    hi = s.rolling(d(365), min_periods=d(30)).max()
    feats["drawdown_from_1y_high_pct"] = ((s / hi) - 1).to_numpy() * 100
    vol = signals.realized_vol(c, d(30), ppy)
    feats["realized_vol_30d_pct"] = vol * 100
    feats["vol_percentile_1y"] = (
        pd.Series(vol).rolling(d(365), min_periods=d(60)).rank(pct=True).to_numpy() * 100
    )
    feats["rsi14"] = ta.rsi(c, 14, sequential=True)
    feats["adx14"] = ta.adx(candles, 14, sequential=True).adx
    v = pd.Series(candles[:, VOLUME])
    feats["volume_z_30d"] = ((v - v.rolling(d(30)).mean()) / v.rolling(d(30)).std()).to_numpy()
    return feats


@dataclass
class MarketContext:
    asset: str
    timeframe: str
    as_of: str
    features: dict[str, float | None]
    analysts: list[dict[str, Any]]
    consensus: float
    consensus_method: str
    portfolio: dict[str, Any]
    limits: dict[str, Any]
    recent_decisions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": self.asset,
            "timeframe": self.timeframe,
            "as_of": self.as_of,
            "market_features": self.features,
            "quant_analysts": self.analysts,
            "quant_consensus": {"target_exposure": round(self.consensus, 3), "method": self.consensus_method},
            "portfolio": self.portfolio,
            "limits": self.limits,
            "recent_decisions": self.recent_decisions,
        }


def _clean(x: float) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 3)


def consensus_exposure(
    sigs: dict[str, float],
    vol: float,
    target_vol: float,
    max_exposure: float,
    allow_short: bool,
    weights: dict[str, float] | None = None,
) -> float:
    w = weights or {k: v["weight"] for k, v in ANALYSTS.items()}
    num = den = 0.0
    for k, v in sigs.items():
        if v is None or not np.isfinite(v):
            continue
        num += w.get(k, 0.0) * v
        den += w.get(k, 0.0)
    if den <= 0:
        return 0.0
    direction = num / den
    if not allow_short:
        direction = max(direction, 0.0)
    scale = min(max_exposure, target_vol / vol) if vol and vol > 0 else 0.0
    return float(np.clip(direction * scale, -max_exposure, max_exposure))


def build_context(
    *,
    symbol: str,
    timeframe: str,
    ts_ms: int,
    feats: dict[str, float],
    sigs: dict[str, float],
    consensus: float,
    target_vol: float,
    portfolio: dict[str, Any],
    limits: dict[str, Any],
    anonymize: bool = True,
    recent: list[dict[str, Any]] | None = None,
) -> MarketContext:
    analysts = []
    for k, v in sigs.items():
        meta = ANALYSTS.get(k, {})
        analysts.append(
            {
                "name": k,
                "signal": _clean(v),
                "weight": meta.get("weight"),
                "description": meta.get("description", ""),
                "evidence": meta.get("evidence", ""),
            }
        )
    return MarketContext(
        asset="ASSET" if anonymize else symbol,
        timeframe=timeframe,
        as_of="t (current bar close)" if anonymize else pd.Timestamp(ts_ms, unit="ms", tz="UTC").isoformat(),
        features={k: _clean(v) for k, v in feats.items()},
        analysts=analysts,
        consensus=consensus,
        consensus_method=f"weighted analyst average x min(max_exposure, {target_vol:.0%} target vol / realized vol)",
        portfolio=portfolio,
        limits=limits,
        recent_decisions=recent or [],
    )


def latest(arrs: dict[str, np.ndarray], i: int) -> dict[str, float]:
    return {k: float(v[i]) if i < len(v) else float("nan") for k, v in arrs.items()}


def ts_of(candles: np.ndarray, i: int, timeframe: str) -> int:
    return int(candles[i, TS]) + tfs.to_ms(timeframe)
