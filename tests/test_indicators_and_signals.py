"""Indicators and signal models must be causal: value[i] depends only on data[:i+1]."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradebot import indicators as ta
from tradebot.strategies import signals

from .conftest import HOUR, gbm, make_candles


@pytest.fixture(scope="module")
def candles():
    return make_candles(gbm(600, sigma=0.02, seed=3), spread=0.005)


SEQ_INDICATORS = {
    "sma": lambda c: ta.sma(c, 20, sequential=True),
    "ema": lambda c: ta.ema(c, 20, sequential=True),
    "rma": lambda c: ta.rma(c, 14, sequential=True),
    "rsi": lambda c: ta.rsi(c, 14, sequential=True),
    "atr": lambda c: ta.atr(c, 14, sequential=True),
    "stddev": lambda c: ta.stddev(c, 20, sequential=True),
    "zscore": lambda c: ta.zscore(c, 20, sequential=True),
    "roc": lambda c: ta.roc(c, 10, sequential=True),
    "rvol": lambda c: ta.realized_vol(c, 30, sequential=True),
    "bb_up": lambda c: ta.bollinger_bands(c, 20, sequential=True).upperband,
    "donchian_lo": lambda c: ta.donchian(c, 20, sequential=True).lowerband,
    "macd": lambda c: ta.macd(c, sequential=True).hist,
    "adx": lambda c: ta.adx(c, 14, sequential=True).adx,
    "supertrend": lambda c: ta.supertrend(c, 10, 3, sequential=True).trend,
    "keltner": lambda c: ta.keltner(c, 20, sequential=True).upperband,
}

SIGNALS = {
    "tsmom": lambda c: signals.tsmom(c[:, 4], [5, 10, 20, 40]),
    "ma_trend": lambda c: signals.ma_trend(c[:, 4], 30),
    "donchian": lambda c: signals.donchian_state(c, 20, 10),
    "donchian_ls": lambda c: signals.donchian_state(c, 20, 10, allow_short=True),
    "vol_target": lambda c: signals.vol_target_scale(c[:, 4], 0.5, 30, 365, 2.0),
    "ewma_vol": lambda c: signals.ewma_vol(c[:, 4], 10, 365),
}


@pytest.mark.parametrize("name", list(SEQ_INDICATORS) + list(SIGNALS))
def test_causality(candles, name):
    fn = SEQ_INDICATORS.get(name) or SIGNALS[name]
    full = np.asarray(fn(candles), dtype=float)
    for cut in (80, 150, 333, 599):
        part = np.asarray(fn(candles[: cut + 1]), dtype=float)
        a, b = full[: cut + 1], part
        both = ~np.isnan(a) & ~np.isnan(b)
        assert np.array_equal(np.isnan(a), np.isnan(b)), f"{name}: NaN pattern differs at cut {cut}"
        np.testing.assert_allclose(
            a[both], b[both], rtol=1e-9, atol=1e-9, err_msg=f"{name} leaks future data"
        )


def test_ema_matches_pandas_after_warmup(candles):
    c = candles[:, 4]
    ours = ta.ema(c, 10, sequential=True)
    # TA-Lib style: seeded with SMA(10) at index 9
    seed = c[:10].mean()
    ref = [seed]
    for x in c[10:]:
        ref.append(2 / 11 * x + (1 - 2 / 11) * ref[-1])
    np.testing.assert_allclose(ours[9:], ref, rtol=1e-10)
    assert np.isnan(ours[:9]).all()


def test_rsi_bounds_and_sma_value(candles):
    r = ta.rsi(candles, 14, sequential=True)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100
    assert ta.sma(candles, 5) == pytest.approx(candles[-5:, 4].mean())


def test_crossed():
    a = np.array([1, 2, 3, 2, 1.0])
    assert ta.crossed(a, 2.5, "above", sequential=True).tolist() == [False, False, True, False, False]
    assert ta.crossed(a, 2.5, "below", sequential=True).tolist() == [False, False, False, True, False]


def test_volatility_breakout_levels_use_previous_day_range():
    closes = np.concatenate([np.linspace(100, 110, 24), np.linspace(110, 105, 24)])
    c = make_candles(closes, step_ms=HOUR)
    _, level, last = signals.volatility_breakout_levels(c, 0.5, HOUR)
    day0 = c[:24]
    rng0 = day0[:, 2].max() - day0[:, 3].min()
    assert level[24] == pytest.approx(c[24, 1] + 0.5 * rng0)
    assert last[23] and last[47] and not last[24]


def test_tsmom_is_bounded(candles):
    s = signals.tsmom(candles[:, 4], [5, 10, 20])
    s = s[~np.isnan(s)]
    assert s.min() >= -1 and s.max() <= 1


def test_resample_and_validation():
    from tradebot.core import candles as C

    c = make_candles(np.arange(1, 49, dtype=float), step_ms=HOUR)
    d = C.resample(c, "1d")
    assert len(d) == 2 or len(d) == 3
    assert C.validate(c, "1h") == []
    broken = c.copy()
    broken[5, 2] = 0.1  # high below open/close
    assert C.validate(broken, "1h")
    df = C.to_dataframe(c)
    assert isinstance(df.index, pd.DatetimeIndex)
    np.testing.assert_allclose(C.from_dataframe(df), c)
