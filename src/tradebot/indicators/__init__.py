"""Vectorized technical indicators.

Every function accepts either a candle array (``[ts, open, high, low, close, volume]``)
or a 1-D numpy array, and follows Jesse's convention:

* ``sequential=False`` (default) -> return the latest value (float / NamedTuple of floats)
* ``sequential=True``            -> return the full series (np.ndarray / NamedTuple of arrays)

All indicators are *causal*: the value at index ``i`` only depends on data up to ``i``.
This is verified in ``tests/test_indicators.py`` by recomputing on truncated inputs.
Moving averages are seeded with an SMA of the first ``period`` values (TA-Lib behaviour)
and are ``NaN`` during the warm-up period.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np
import pandas as pd
from scipy.signal import lfilter

from ..core.candles import CLOSE, HIGH, LOW, OPEN, VOLUME

__all__ = [
    "adx",
    "atr",
    "bollinger_bands",
    "crossed",
    "donchian",
    "ema",
    "get_source",
    "highest",
    "keltner",
    "lowest",
    "macd",
    "realized_vol",
    "rma",
    "roc",
    "rsi",
    "sma",
    "stddev",
    "supertrend",
    "true_range",
    "zscore",
]


class BollingerBands(NamedTuple):
    upperband: np.ndarray | float
    middleband: np.ndarray | float
    lowerband: np.ndarray | float


class DonchianChannel(NamedTuple):
    upperband: np.ndarray | float
    middleband: np.ndarray | float
    lowerband: np.ndarray | float


class MACD(NamedTuple):
    macd: np.ndarray | float
    signal: np.ndarray | float
    hist: np.ndarray | float


class ADX(NamedTuple):
    adx: np.ndarray | float
    plus_di: np.ndarray | float
    minus_di: np.ndarray | float


class SuperTrend(NamedTuple):
    trend: np.ndarray | float
    direction: np.ndarray | float  # +1 up-trend, -1 down-trend


# --------------------------------------------------------------------------- helpers


def get_source(candles: np.ndarray, source_type: str = "close") -> np.ndarray:
    """Pick a price series out of a candle array (or pass a 1-D array through)."""
    arr = np.asarray(candles, dtype="float64")
    if arr.ndim == 1:
        return arr
    o, h, low, c = arr[:, OPEN], arr[:, HIGH], arr[:, LOW], arr[:, CLOSE]
    match source_type:
        case "close":
            return c
        case "open":
            return o
        case "high":
            return h
        case "low":
            return low
        case "volume":
            return arr[:, VOLUME]
        case "hl2":
            return (h + low) / 2
        case "hlc3":
            return (h + low + c) / 3
        case "ohlc4":
            return (o + h + low + c) / 4
    raise ValueError(f"unknown source_type {source_type!r}")


def _ret(x: np.ndarray, sequential: bool) -> np.ndarray | float:
    if sequential:
        return x
    return float(x[-1]) if len(x) else float("nan")


def _first_valid(x: np.ndarray) -> int:
    idx = np.flatnonzero(~np.isnan(x))
    return int(idx[0]) if idx.size else len(x)


def _ewm(x: np.ndarray, alpha: float, period: int) -> np.ndarray:
    """Exponential smoothing seeded with the SMA of the first `period` valid values."""
    out = np.full(len(x), np.nan)
    start = _first_valid(x)
    seed_end = start + period
    if seed_end > len(x):
        return out
    seed = float(np.mean(x[start:seed_end]))
    out[seed_end - 1] = seed
    rest = x[seed_end:]
    if rest.size:
        if np.isnan(rest).any():  # fall back to a safe loop when gaps exist
            prev = seed
            for i, v in enumerate(rest, start=seed_end):
                prev = prev if np.isnan(v) else alpha * v + (1 - alpha) * prev
                out[i] = prev
        else:
            y, _ = lfilter([alpha], [1.0, -(1.0 - alpha)], rest, zi=[(1.0 - alpha) * seed])
            out[seed_end:] = y
    return out


def _rolling(x: np.ndarray, period: int, fn: str) -> np.ndarray:
    s = pd.Series(x)
    r = getattr(s.rolling(period, min_periods=period), fn)()
    return r.to_numpy(dtype="float64")


# --------------------------------------------------------------------------- moving averages


def sma(candles: np.ndarray, period: int = 20, source_type: str = "close", sequential: bool = False):
    x = get_source(candles, source_type)
    return _ret(_rolling(x, period, "mean"), sequential)


def ema(candles: np.ndarray, period: int = 20, source_type: str = "close", sequential: bool = False):
    x = get_source(candles, source_type)
    return _ret(_ewm(x, 2.0 / (period + 1.0), period), sequential)


def rma(candles: np.ndarray, period: int = 14, source_type: str = "close", sequential: bool = False):
    """Wilder's moving average (alpha = 1/period)."""
    x = get_source(candles, source_type)
    return _ret(_ewm(x, 1.0 / period, period), sequential)


# --------------------------------------------------------------------------- oscillators / volatility


def rsi(candles: np.ndarray, period: int = 14, source_type: str = "close", sequential: bool = False):
    x = get_source(candles, source_type)
    d = np.diff(x, prepend=np.nan)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    up[0] = dn[0] = np.nan
    au = _ewm(up, 1.0 / period, period)
    ad = _ewm(dn, 1.0 / period, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = au / ad
        out = 100.0 - 100.0 / (1.0 + rs)
    out = np.where((ad == 0) & (au > 0), 100.0, out)
    out = np.where((ad == 0) & (au == 0), 50.0, out)
    return _ret(out, sequential)


def true_range(candles: np.ndarray, sequential: bool = False):
    h, low, c = candles[:, HIGH], candles[:, LOW], candles[:, CLOSE]
    pc = np.concatenate(([np.nan], c[:-1]))
    tr = np.nanmax(np.vstack([h - low, np.abs(h - pc), np.abs(low - pc)]), axis=0)
    return _ret(tr, sequential)


def atr(candles: np.ndarray, period: int = 14, sequential: bool = False):
    tr = true_range(candles, sequential=True)
    return _ret(_ewm(tr, 1.0 / period, period), sequential)


def stddev(candles: np.ndarray, period: int = 20, source_type: str = "close", sequential: bool = False):
    x = get_source(candles, source_type)
    return _ret(_rolling(x, period, "std"), sequential)


def zscore(candles: np.ndarray, period: int = 20, source_type: str = "close", sequential: bool = False):
    x = get_source(candles, source_type)
    m = _rolling(x, period, "mean")
    s = _rolling(x, period, "std")
    with np.errstate(divide="ignore", invalid="ignore"):
        z = (x - m) / s
    return _ret(z, sequential)


def roc(candles: np.ndarray, period: int = 10, source_type: str = "close", sequential: bool = False):
    """Rate of change as a fraction: close / close[-period] - 1."""
    x = get_source(candles, source_type)
    out = np.full(len(x), np.nan)
    if len(x) > period:
        out[period:] = x[period:] / x[:-period] - 1.0
    return _ret(out, sequential)


def realized_vol(
    candles: np.ndarray,
    period: int = 30,
    periods_per_year: float = 365.0,
    source_type: str = "close",
    sequential: bool = False,
):
    """Annualized close-to-close volatility of simple returns."""
    x = get_source(candles, source_type)
    r = np.diff(x, prepend=np.nan) / np.concatenate(([np.nan], x[:-1]))
    vol = _rolling(r, period, "std") * np.sqrt(periods_per_year)
    return _ret(vol, sequential)


def highest(candles: np.ndarray, period: int = 20, source_type: str = "high", sequential: bool = False):
    x = get_source(candles, source_type)
    return _ret(_rolling(x, period, "max"), sequential)


def lowest(candles: np.ndarray, period: int = 20, source_type: str = "low", sequential: bool = False):
    x = get_source(candles, source_type)
    return _ret(_rolling(x, period, "min"), sequential)


# --------------------------------------------------------------------------- bands / channels


def bollinger_bands(
    candles: np.ndarray,
    period: int = 20,
    devup: float = 2.0,
    devdn: float = 2.0,
    source_type: str = "close",
    sequential: bool = False,
) -> BollingerBands:
    x = get_source(candles, source_type)
    mid = _rolling(x, period, "mean")
    sd = pd.Series(x).rolling(period, min_periods=period).std(ddof=0).to_numpy()
    up, lo = mid + devup * sd, mid - devdn * sd
    if sequential:
        return BollingerBands(up, mid, lo)
    return BollingerBands(float(up[-1]), float(mid[-1]), float(lo[-1]))


def donchian(candles: np.ndarray, period: int = 20, sequential: bool = False) -> DonchianChannel:
    """Donchian channel over the last `period` candles *including* the current one.

    For breakout rules compare the current close with the *previous* bar's channel
    (``donchian(candles[:-1], ...)`` or index ``[-2]`` of the sequential output).
    """
    up = _rolling(candles[:, HIGH], period, "max")
    lo = _rolling(candles[:, LOW], period, "min")
    mid = (up + lo) / 2
    if sequential:
        return DonchianChannel(up, mid, lo)
    return DonchianChannel(float(up[-1]), float(mid[-1]), float(lo[-1]))


def keltner(
    candles: np.ndarray, period: int = 20, multiplier: float = 2.0, sequential: bool = False
) -> BollingerBands:
    mid = ema(candles, period, sequential=True)
    a = atr(candles, period, sequential=True)
    up, lo = mid + multiplier * a, mid - multiplier * a
    if sequential:
        return BollingerBands(up, mid, lo)
    return BollingerBands(float(up[-1]), float(mid[-1]), float(lo[-1]))


# --------------------------------------------------------------------------- trend


def macd(
    candles: np.ndarray,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
    source_type: str = "close",
    sequential: bool = False,
) -> MACD:
    x = get_source(candles, source_type)
    line = _ewm(x, 2 / (fast_period + 1), fast_period) - _ewm(x, 2 / (slow_period + 1), slow_period)
    sig = _ewm(line, 2 / (signal_period + 1), signal_period)
    hist = line - sig
    if sequential:
        return MACD(line, sig, hist)
    return MACD(float(line[-1]), float(sig[-1]), float(hist[-1]))


def adx(candles: np.ndarray, period: int = 14, sequential: bool = False) -> ADX:
    h, low = candles[:, HIGH], candles[:, LOW]
    up_move = np.diff(h, prepend=np.nan)
    down_move = -np.diff(low, prepend=np.nan)
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm[0] = minus_dm[0] = np.nan
    tr = true_range(candles, sequential=True)
    tr[0] = np.nan
    atr_ = _ewm(tr, 1 / period, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100 * _ewm(plus_dm, 1 / period, period) / atr_
        mdi = 100 * _ewm(minus_dm, 1 / period, period) / atr_
        dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
    dx = np.where(np.isfinite(dx), dx, np.nan)
    adx_ = _ewm(dx, 1 / period, period)
    if sequential:
        return ADX(adx_, pdi, mdi)
    return ADX(float(adx_[-1]), float(pdi[-1]), float(mdi[-1]))


def supertrend(
    candles: np.ndarray, period: int = 10, factor: float = 3.0, sequential: bool = False
) -> SuperTrend:
    a = atr(candles, period, sequential=True)
    hl2 = (candles[:, HIGH] + candles[:, LOW]) / 2
    c = candles[:, CLOSE]
    n = len(c)
    upper = hl2 + factor * a
    lower = hl2 - factor * a
    trend = np.full(n, np.nan)
    direction = np.full(n, np.nan)
    fu, fl = np.nan, np.nan
    d = 1.0
    for i in range(n):
        if np.isnan(a[i]):
            continue
        if np.isnan(fu):
            fu, fl = upper[i], lower[i]
        else:
            fu = upper[i] if (upper[i] < fu or c[i - 1] > fu) else fu
            fl = lower[i] if (lower[i] > fl or c[i - 1] < fl) else fl
        if d == 1.0 and c[i] < fl:
            d = -1.0
        elif d == -1.0 and c[i] > fu:
            d = 1.0
        trend[i] = fl if d == 1.0 else fu
        direction[i] = d
    if sequential:
        return SuperTrend(trend, direction)
    return SuperTrend(float(trend[-1]), float(direction[-1]))


def crossed(
    series1: np.ndarray, series2: np.ndarray | float, direction: str | None = None, sequential: bool = False
):
    """True where series1 crosses series2 ("above", "below" or either)."""
    s1 = np.asarray(series1, dtype="float64")
    s2 = np.full_like(s1, float(series2)) if np.isscalar(series2) else np.asarray(series2, dtype="float64")
    prev_diff = np.concatenate(([np.nan], (s1 - s2)[:-1]))
    diff = s1 - s2
    above = (prev_diff <= 0) & (diff > 0)
    below = (prev_diff >= 0) & (diff < 0)
    out = above if direction == "above" else below if direction == "below" else (above | below)
    if sequential:
        return out
    return bool(out[-1]) if len(out) else False
