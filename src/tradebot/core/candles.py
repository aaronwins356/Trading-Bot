"""Candle arrays.

Candles are stored as float64 numpy arrays with the CCXT column order:

    [timestamp_ms, open, high, low, close, volume]

NOTE for Jesse users: Jesse orders columns as [ts, open, close, high, low, volume].
Use the named constants below (or the helpers) instead of raw column numbers and
your code will be portable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import timeframes as tfs

TS, OPEN, HIGH, LOW, CLOSE, VOLUME = 0, 1, 2, 3, 4, 5
COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def from_dataframe(df: pd.DataFrame) -> np.ndarray:
    """Convert an OHLCV DataFrame (DatetimeIndex or 'timestamp' column) to a candle array."""
    if "timestamp" in df.columns:
        ts = df["timestamp"].to_numpy(dtype="int64")
        if ts.size and ts.max() < 10_000_000_000:  # seconds -> ms
            ts = ts * 1000
    else:
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        ts = (idx.as_unit("ms").asi8).astype("int64")
    vol = df["volume"].to_numpy(dtype="float64") if "volume" in df.columns else np.zeros(len(df))
    out = np.column_stack(
        [
            ts.astype("float64"),
            df["open"].to_numpy(dtype="float64"),
            df["high"].to_numpy(dtype="float64"),
            df["low"].to_numpy(dtype="float64"),
            df["close"].to_numpy(dtype="float64"),
            vol,
        ]
    )
    return out


def to_dataframe(candles: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame(candles[:, 1:], columns=COLUMNS[1:])
    df.index = pd.to_datetime(candles[:, TS].astype("int64"), unit="ms", utc=True)
    df.index.name = "time"
    return df


def resample(candles: np.ndarray, timeframe: str) -> np.ndarray:
    """Aggregate candles to a higher timeframe. Bars are labelled by their open time.

    The final (possibly incomplete) bucket is kept; callers that need only
    closed candles should use :func:`closed_only`.
    """
    if len(candles) == 0:
        return candles.copy()
    ms = tfs.to_ms(timeframe)
    ts = candles[:, TS].astype("int64")
    if timeframe.endswith("w"):
        buckets = np.array([tfs.floor_ts(int(t), timeframe) for t in ts], dtype="int64")
    else:
        buckets = (ts // ms) * ms
    # boundaries where bucket changes
    change = np.flatnonzero(np.diff(buckets)) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(candles)]))
    out = np.empty((len(starts), 6), dtype="float64")
    out[:, TS] = buckets[starts]
    out[:, OPEN] = candles[starts, OPEN]
    out[:, CLOSE] = candles[ends - 1, CLOSE]
    out[:, HIGH] = np.maximum.reduceat(candles[:, HIGH], starts)
    out[:, LOW] = np.minimum.reduceat(candles[:, LOW], starts)
    out[:, VOLUME] = np.add.reduceat(candles[:, VOLUME], starts)
    return out


def closed_only(candles: np.ndarray, timeframe: str, now_ms: int) -> np.ndarray:
    """Drop the trailing candle if it has not closed yet at `now_ms`."""
    if len(candles) and candles[-1, TS] + tfs.to_ms(timeframe) > now_ms:
        return candles[:-1]
    return candles


def validate(candles: np.ndarray, timeframe: str | None = None) -> list[str]:
    """Return a list of data-quality problems (empty list == clean)."""
    problems: list[str] = []
    if candles.ndim != 2 or candles.shape[1] != 6:
        return [f"expected shape (n, 6), got {candles.shape}"]
    if np.isnan(candles).any():
        problems.append(f"{int(np.isnan(candles).any(axis=1).sum())} rows contain NaN")
    ts = candles[:, TS]
    if len(ts) > 1 and (np.diff(ts) <= 0).any():
        problems.append("timestamps are not strictly increasing")
    if timeframe and len(ts) > 1:
        gaps = np.diff(ts) != tfs.to_ms(timeframe)
        if gaps.any():
            problems.append(f"{int(gaps.sum())} gaps/irregular intervals for timeframe {timeframe}")
    hi_bad = candles[:, HIGH] < np.maximum(candles[:, OPEN], candles[:, CLOSE]) - 1e-9
    lo_bad = candles[:, LOW] > np.minimum(candles[:, OPEN], candles[:, CLOSE]) + 1e-9
    if hi_bad.any() or lo_bad.any():
        problems.append(f"{int(hi_bad.sum() + lo_bad.sum())} rows with inconsistent high/low")
    if (candles[:, 1:5] <= 0).any():
        problems.append("non-positive prices present")
    return problems


def slice_time(candles: np.ndarray, start_ms: int | None = None, end_ms: int | None = None) -> np.ndarray:
    ts = candles[:, TS]
    lo = 0 if start_ms is None else int(np.searchsorted(ts, start_ms, side="left"))
    hi = len(ts) if end_ms is None else int(np.searchsorted(ts, end_ms, side="left"))
    return candles[lo:hi]


def fill_gaps(candles: np.ndarray, timeframe: str) -> np.ndarray:
    """Insert flat zero-volume candles for missing intervals (exchange downtime)."""
    if len(candles) < 2:
        return candles
    ms = tfs.to_ms(timeframe)
    ts = candles[:, TS].astype("int64")
    full = np.arange(ts[0], ts[-1] + ms, ms, dtype="int64")
    if len(full) == len(ts):
        return candles
    idx = np.searchsorted(ts, full, side="right") - 1
    out = np.empty((len(full), 6))
    out[:, TS] = full
    prev_close = candles[idx, CLOSE]
    present = ts[idx] == full
    out[:, OPEN] = np.where(present, candles[idx, OPEN], prev_close)
    out[:, HIGH] = np.where(present, candles[idx, HIGH], prev_close)
    out[:, LOW] = np.where(present, candles[idx, LOW], prev_close)
    out[:, CLOSE] = np.where(present, candles[idx, CLOSE], prev_close)
    out[:, VOLUME] = np.where(present, candles[idx, VOLUME], 0.0)
    return out
