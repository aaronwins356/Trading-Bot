"""Timeframe helpers (CCXT-style strings: 1m, 5m, 15m, 1h, 4h, 1d, 1w)."""

from __future__ import annotations

import re

_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_PANDAS_RULE = {"m": "min", "h": "h", "d": "D", "w": "W-MON"}

TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"]


def parse(tf: str) -> tuple[int, str]:
    m = re.fullmatch(r"(\d+)([mhdw])", tf.strip())
    if not m:
        raise ValueError(f"Invalid timeframe '{tf}'. Use forms like 1m, 15m, 1h, 4h, 1d, 1w.")
    return int(m.group(1)), m.group(2)


def to_ms(tf: str) -> int:
    n, unit = parse(tf)
    return n * _UNIT_MS[unit]


def to_minutes(tf: str) -> int:
    return to_ms(tf) // 60_000


def to_pandas_rule(tf: str) -> str:
    n, unit = parse(tf)
    if unit == "w":
        return "W-MON" if n == 1 else f"{n * 7}D"
    return f"{n}{_PANDAS_RULE[unit]}"


def bars_per_year(tf: str, trading_days: int = 365) -> float:
    """Crypto trades 24/7 -> 365 days/year by default."""
    return trading_days * 86_400_000 / to_ms(tf)


def floor_ts(ts_ms: int, tf: str) -> int:
    """Floor a timestamp to the start of its timeframe bucket (UTC, epoch aligned; weeks start Monday)."""
    ms = to_ms(tf)
    if tf.endswith("w"):
        # epoch (1970-01-01) was a Thursday; shift so buckets start on Monday 00:00 UTC
        offset = 4 * 86_400_000
        return ((ts_ms - offset) // ms) * ms + offset
    return (ts_ms // ms) * ms


def is_higher_or_equal(tf_a: str, tf_b: str) -> bool:
    return to_ms(tf_a) >= to_ms(tf_b)
