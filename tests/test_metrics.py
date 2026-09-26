from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tradebot.backtest import metrics as M


def _equity(rets, start="2020-01-01"):
    idx = pd.date_range(start, periods=len(rets) + 1, freq="1D", tz="UTC")
    return pd.Series(10_000 * np.cumprod(np.concatenate(([1.0], 1 + np.asarray(rets)))), index=idx)


def test_sharpe_and_cagr_on_known_series():
    rng = np.random.default_rng(0)
    r = rng.normal(0.001, 0.02, 3650)
    eq = _equity(r)
    m = M.compute_metrics(eq)
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * np.sqrt(365), abs=1e-3)
    years = 3650 / 365
    assert m["cagr_pct"] == pytest.approx(((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100, abs=0.01)


def test_max_drawdown():
    eq = _equity([0.1, -0.5, 0.2, 0.1])
    assert M.compute_metrics(eq)["max_drawdown_pct"] == pytest.approx(-50.0)


def test_psr_and_dsr_behave():
    rng = np.random.default_rng(1)
    good = rng.normal(0.002, 0.02, 2000)
    noise = rng.normal(0.0, 0.02, 2000)
    assert M.probabilistic_sharpe_ratio(good) > 0.95
    assert 0.02 < M.probabilistic_sharpe_ratio(noise) < 0.98
    # more trials -> higher bar -> lower DSR
    d1 = M.deflated_sharpe_ratio(good, 1)
    d100 = M.deflated_sharpe_ratio(good, 100, trial_sharpes=rng.normal(0, 0.03, 100))
    assert d100 < d1


def test_expected_max_sharpe_grows_with_trials():
    assert M.expected_max_sharpe(1000, 0.01) > M.expected_max_sharpe(10, 0.01) > 0


def test_monthly_and_yearly_returns_shapes():
    eq = _equity(np.full(400, 0.001))
    m = M.compute_metrics(eq)
    assert "2020" in m["yearly_returns"] and "2021" in m["yearly_returns"]
    assert "1" in m["monthly_returns"]["2020"]


def test_daily_returns_align_across_timeframes():
    """Regression: equity is stamped at bar close; 1d and 4h runs of the same buy & hold
    must produce identically labelled daily returns (previously off by one day)."""
    from tradebot.backtest.engine import BacktestConfig, run_backtest
    from tradebot.core import candles as C
    from tradebot.strategy.base import Strategy

    from .conftest import HOUR, gbm, make_candles

    class Hold(Strategy):
        def after(self):
            self.order_target_exposure(1.0)

    h = make_candles(gbm(24 * 400, mu=0.0, sigma=0.01, seed=2), step_ms=HOUR)
    cfg = BacktestConfig(fee_maker=0, fee_taker=0, slippage_bps=0)
    r4 = run_backtest(Hold, C.resample(h, "4h"), "4h", config=cfg)
    r1 = run_backtest(Hold, C.resample(h, "1d"), "1d", config=cfg)
    a, b = M.daily_returns(r4.equity), M.daily_returns(r1.equity)
    joined = pd.concat([a, b], axis=1, keys=["4h", "1d"]).dropna().iloc[2:]
    assert joined["4h"].corr(joined["1d"]) > 0.97
