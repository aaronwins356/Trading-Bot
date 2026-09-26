from __future__ import annotations

import numpy as np
import pytest

DAY = 86_400_000
HOUR = 3_600_000


def make_candles(
    closes,
    start_ms: int = 1_600_000_000_000 - (1_600_000_000_000 % DAY),
    step_ms: int = DAY,
    spread: float = 0.0,
):
    """Build candles from closes: open = previous close, high/low = max/min(open, close) +/- spread."""
    closes = np.asarray(closes, dtype="float64")
    opens = np.concatenate(([closes[0]], closes[:-1]))
    highs = np.maximum(opens, closes) * (1 + spread)
    lows = np.minimum(opens, closes) * (1 - spread)
    ts = start_ms + np.arange(len(closes)) * step_ms
    return np.column_stack([ts, opens, highs, lows, closes, np.ones(len(closes))])


def gbm(n: int, mu: float = 0.0005, sigma: float = 0.03, seed: int = 7, s0: float = 100.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(mu, sigma, n)
    return s0 * np.exp(np.cumsum(r))


@pytest.fixture
def daily_gbm():
    return make_candles(gbm(1500), spread=0.01)
