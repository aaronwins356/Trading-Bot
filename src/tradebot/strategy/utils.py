"""Position-sizing and helper utilities (names mirror ``jesse.utils``)."""

from __future__ import annotations

import math

import numpy as np


def _floor(x: float, precision: int) -> float:
    f = 10**precision
    return math.floor(x * f) / f


def size_to_qty(position_size: float, entry_price: float, precision: int = 8, fee_rate: float = 0.0) -> float:
    """Convert a quote-currency position size into a quantity, leaving room for fees."""
    if entry_price <= 0 or not math.isfinite(entry_price):
        raise ValueError(f"invalid entry price {entry_price}")
    size = position_size * (1.0 - fee_rate * 3.0) if fee_rate else position_size
    return max(_floor(size / entry_price, precision), 0.0)


def qty_to_size(qty: float, price: float) -> float:
    return qty * price


def risk_to_qty(
    capital: float,
    risk_per_capital: float,
    entry_price: float,
    stop_loss_price: float,
    precision: int = 8,
    fee_rate: float = 0.0,
) -> float:
    """Quantity such that hitting the stop loses ``risk_per_capital`` percent of capital.

    >>> risk_to_qty(10_000, 1, 100, 95)  # risk 1% = $100, $5 per unit
    20.0
    """
    risk_per_unit = abs(entry_price - stop_loss_price)
    if risk_per_unit <= 0:
        raise ValueError("entry and stop-loss prices must differ")
    risk_amount = capital * risk_per_capital / 100.0
    qty = risk_amount / risk_per_unit
    # never size above the capital itself (un-leveraged)
    qty = min(qty, capital / entry_price)
    if fee_rate:
        qty *= 1.0 - fee_rate * 3.0
    return max(_floor(qty, precision), 0.0)


def vol_target_qty(
    equity: float,
    price: float,
    realized_vol: float,
    target_vol: float,
    max_leverage: float = 1.0,
    precision: int = 8,
) -> float:
    """Volatility targeting: scale exposure so the position's annualized vol ~= target_vol."""
    if not (realized_vol > 0) or not math.isfinite(realized_vol):
        return 0.0
    weight = min(target_vol / realized_vol, max_leverage)
    return max(_floor(equity * weight / price, precision), 0.0)


def kelly_criterion(win_rate: float, ratio_avg_win_loss: float) -> float:
    """Full Kelly fraction. Use a fraction of it (e.g. 1/4) in practice."""
    if ratio_avg_win_loss <= 0:
        return 0.0
    return win_rate - (1.0 - win_rate) / ratio_avg_win_loss


def estimate_risk(entry_price: float, stop_price: float) -> float:
    return abs(entry_price - stop_price)


def z_score(series: np.ndarray) -> np.ndarray:
    s = np.asarray(series, dtype="float64")
    return (s - s.mean()) / s.std()


def anchor_timeframe(timeframe: str) -> str:
    """Jesse's rule of thumb for a higher 'anchor' timeframe (~4-6x)."""
    return {
        "1m": "5m",
        "3m": "15m",
        "5m": "30m",
        "15m": "2h",
        "30m": "3h",
        "1h": "4h",
        "2h": "6h",
        "4h": "1d",
        "6h": "1d",
        "8h": "1d",
        "12h": "1d",
        "1d": "1w",
    }.get(timeframe, "1d")


def sum_floats(a: float, b: float) -> float:
    return float(np.float64(a) + np.float64(b))


def subtract_floats(a: float, b: float) -> float:
    return float(np.float64(a) - np.float64(b))
