"""Causal, vectorized signal models ("alpha models").

Each function maps price history to a *desired exposure* series in [-1, 1]
where element ``i`` only uses data up to ``i`` (verified by
``tests/test_signals.py`` and ``tradebot lookahead``).

They are the single source of truth for:

* the tradable strategies in :mod:`tradebot.strategies`, and
* the "quant analyst" panel that the JEV decision engine summarises for the LLM.

This mirrors the Alpha-model / Portfolio-construction split of QuantConnect's
Algorithm Framework.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from ..core.candles import CLOSE, HIGH, LOW, OPEN, TS


def _shift(x: np.ndarray, n: int = 1) -> np.ndarray:
    out = np.full_like(x, np.nan, dtype="float64")
    if n < len(x):
        out[n:] = x[:-n] if n > 0 else x
    return out


def returns(close: np.ndarray) -> np.ndarray:
    r = np.full(len(close), np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    return r


def realized_vol(close: np.ndarray, window: int, periods_per_year: float) -> np.ndarray:
    r = pd.Series(returns(close))
    return (r.rolling(window, min_periods=max(5, window // 2)).std() * np.sqrt(periods_per_year)).to_numpy()


def ewma_vol(close: np.ndarray, halflife: float, periods_per_year: float) -> np.ndarray:
    """Exponentially weighted volatility (RiskMetrics style) - reacts faster to shocks."""
    r = pd.Series(returns(close))
    return (
        r.ewm(halflife=halflife, min_periods=max(5, int(halflife))).std() * np.sqrt(periods_per_year)
    ).to_numpy()


def tsmom(close: np.ndarray, lookbacks: Sequence[int]) -> np.ndarray:
    """Time-series momentum ensemble: mean of sign(return over L) across lookbacks, in [-1, 1].

    Moskowitz, Ooi & Pedersen (2012); multi-horizon averaging reduces parameter risk.
    """
    close = np.asarray(close, dtype="float64")
    sigs = []
    for lb in lookbacks:
        s = np.full(len(close), np.nan)
        if len(close) > lb:
            s[lb:] = np.sign(close[lb:] / close[:-lb] - 1.0)
        sigs.append(s)
    if not sigs:
        return np.full(len(close), np.nan)
    stacked = np.vstack(sigs)
    # require the longest horizon to be available (avoids a warm-up period driven by one short lookback)
    valid = ~np.isnan(stacked).any(axis=0)
    out = np.full(len(close), np.nan)
    out[valid] = stacked[:, valid].mean(axis=0)
    return out


def ma_trend(close: np.ndarray, period: int) -> np.ndarray:
    """+1 above the simple moving average, -1 below."""
    ma = pd.Series(close).rolling(period, min_periods=period).mean().to_numpy()
    out = np.where(close > ma, 1.0, -1.0)
    out[np.isnan(ma)] = np.nan
    return out


def donchian_state(candles: np.ndarray, entry: int, exit_: int, allow_short: bool = False) -> np.ndarray:
    """Turtle breakout state machine.

    Long when the close breaks above the highest high of the *previous* ``entry`` bars;
    flat when it closes below the lowest low of the previous ``exit_`` bars
    (mirror image for shorts). Returns {-1, 0, 1}.
    """
    h, lo, c = candles[:, HIGH], candles[:, LOW], candles[:, CLOSE]
    hh = _shift(pd.Series(h).rolling(entry, min_periods=entry).max().to_numpy())
    ll_exit = _shift(pd.Series(lo).rolling(exit_, min_periods=exit_).min().to_numpy())
    ev = np.full(len(c), np.nan)
    ev[c < ll_exit] = 0.0
    ev[c > hh] = 1.0
    if allow_short:
        ll = _shift(pd.Series(lo).rolling(entry, min_periods=entry).min().to_numpy())
        hh_exit = _shift(pd.Series(h).rolling(exit_, min_periods=exit_).max().to_numpy())
        # separate state machine for shorts, combined below
        ev_s = np.full(len(c), np.nan)
        ev_s[c > hh_exit] = 0.0
        ev_s[c < ll] = -1.0
        short = pd.Series(ev_s).ffill().fillna(0.0).to_numpy()
    long_ = pd.Series(ev).ffill().fillna(0.0).to_numpy()
    if allow_short:
        return np.where(long_ > 0, 1.0, short)
    return long_


def vol_target_scale(
    close: np.ndarray,
    target_vol: float,
    window: int,
    periods_per_year: float,
    max_leverage: float = 1.0,
    halflife: float | None = None,
) -> np.ndarray:
    """Leverage that scales a unit position to ``target_vol`` annualized volatility."""
    vol = (
        ewma_vol(close, halflife, periods_per_year)
        if halflife
        else realized_vol(close, window, periods_per_year)
    )
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = target_vol / vol
    scale = np.where(np.isfinite(scale), scale, np.nan)
    return np.clip(scale, 0.0, max_leverage)


def volatility_breakout_levels(
    candles: np.ndarray, k: float, bar_ms: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Larry Williams volatility breakout on intraday bars.

    For each intraday bar returns (day_open, breakout_level, is_last_bar_of_day), where
    breakout_level = today's open + k * (yesterday's high - yesterday's low).
    Only completed-day information is used for the range (causal).
    """
    ts = candles[:, TS].astype("int64")
    day = ts // 86_400_000
    df = pd.DataFrame({"day": day, "o": candles[:, OPEN], "h": candles[:, HIGH], "l": candles[:, LOW]})
    g = df.groupby("day")
    d_open = g["o"].transform("first").to_numpy()
    daily = g.agg(h=("h", "max"), l=("l", "min"))
    prev_range = (daily["h"] - daily["l"]).shift(1)
    rng = pd.Series(day).map(prev_range).to_numpy(dtype="float64")
    level = d_open + k * rng
    last_bar = ((ts + bar_ms) % 86_400_000) == 0
    return d_open, level, last_bar


def zscore(x: np.ndarray, window: int) -> np.ndarray:
    s = pd.Series(x)
    m = s.rolling(window, min_periods=window).mean()
    sd = s.rolling(window, min_periods=window).std()
    return ((s - m) / sd).to_numpy()


def rsi(close: np.ndarray, period: int) -> np.ndarray:
    from .. import indicators as ta

    return ta.rsi(close, period, sequential=True)
