"""Performance & robustness statistics.

Includes the Probabilistic Sharpe Ratio and Deflated Sharpe Ratio from
Bailey & López de Prado (2012, 2014), which correct a backtest's Sharpe ratio
for sample length, non-normal returns and the number of strategy variants tried
(selection bias / backtest overfitting).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

from ..core.types import ClosedTrade

EULER_GAMMA = 0.5772156649015329


def end_of_period(equity: pd.Series) -> pd.Series:
    """Equity is stamped at bar *close* times; a close at 00:00 on day D+1 is the end of day D.
    Shifting by 1 ms buckets every close into the period it ends, so daily/monthly/yearly
    statistics line up across timeframes (1h, 4h and 1d strategies share the same day labels)."""
    return equity.set_axis(equity.index - pd.Timedelta(milliseconds=1))


def daily_equity(equity: pd.Series) -> pd.Series:
    return end_of_period(equity).resample("1D").last().ffill()


def daily_returns(equity: pd.Series) -> pd.Series:
    """Resample an equity curve (any frequency, UTC DatetimeIndex) to daily simple returns."""
    return daily_equity(equity).pct_change().dropna()


def sharpe(returns: np.ndarray | pd.Series, periods_per_year: float = 365.0, rf: float = 0.0) -> float:
    r = np.asarray(returns, dtype="float64") - rf / periods_per_year
    if r.size < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / r.std(ddof=1) * math.sqrt(periods_per_year))


def sortino(returns: np.ndarray | pd.Series, periods_per_year: float = 365.0) -> float:
    r = np.asarray(returns, dtype="float64")
    downside = np.minimum(r, 0.0)
    dd = math.sqrt(float(np.mean(downside**2))) if r.size else 0.0
    if dd == 0:
        return 0.0
    return float(r.mean() / dd * math.sqrt(periods_per_year))


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def max_drawdown_duration_days(equity: pd.Series) -> float:
    """Longest time (days) spent below a previous equity peak."""
    peak_time = equity.index[0]
    peak = equity.iloc[0]
    longest = pd.Timedelta(0)
    for t, v in equity.items():
        if v >= peak:
            peak, peak_time = v, t
        else:
            longest = max(longest, t - peak_time)
    return longest.total_seconds() / 86400.0


def probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """P(true SR > sr_benchmark) given the sample (per-period, non-annualized SR)."""
    r = np.asarray(returns, dtype="float64")
    n = r.size
    if n < 3 or r.std(ddof=1) == 0:
        return 0.5
    sr = r.mean() / r.std(ddof=1)
    skew = stats.skew(r, bias=False)
    kurt = stats.kurtosis(r, fisher=False, bias=False)
    denom = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2
    if denom <= 0:
        return 0.5
    z = (sr - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """Expected maximum per-period SR among ``n_trials`` unskilled strategies (False Strategy Theorem)."""
    if n_trials <= 1 or sr_variance <= 0:
        return 0.0
    a = stats.norm.ppf(1.0 - 1.0 / n_trials)
    b = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - EULER_GAMMA) * a + EULER_GAMMA * b))


def deflated_sharpe_ratio(
    returns: np.ndarray, n_trials: int, trial_sharpes: Sequence[float] | None = None
) -> float:
    """DSR = PSR evaluated against the SR one would expect from the best of N random trials.

    ``trial_sharpes`` are the *per-period* Sharpe ratios of all variants tried; if omitted,
    the variance is approximated by the sampling variance of the SR estimator.
    """
    r = np.asarray(returns, dtype="float64")
    n = r.size
    if n < 3:
        return 0.0
    if trial_sharpes is not None and len(trial_sharpes) > 1:
        var = float(np.var(np.asarray(trial_sharpes, dtype="float64"), ddof=1))
    else:
        sr = r.mean() / r.std(ddof=1) if r.std(ddof=1) > 0 else 0.0
        var = (1.0 + 0.5 * sr**2) / (n - 1)
    sr0 = expected_max_sharpe(n_trials, var)
    return probabilistic_sharpe_ratio(r, sr0)


def _period_returns(equity: pd.Series, rule: str) -> pd.Series:
    equity = end_of_period(equity)
    m = equity.resample(rule).last()
    rets = m.pct_change()
    if len(m):
        rets.iloc[0] = m.iloc[0] / equity.iloc[0] - 1.0
    counts = equity.resample(rule).count()
    if len(counts) > 1 and counts.iloc[0] == 1:  # bucket holds only the starting-balance anchor
        rets = rets.iloc[1:]
    return rets


def monthly_returns(equity: pd.Series) -> dict[str, dict[str, float]]:
    rets = _period_returns(equity, "ME")
    out: dict[str, dict[str, float]] = {}
    for t, v in rets.items():
        out.setdefault(str(t.year), {})[str(t.month)] = round(float(v) * 100, 2)
    return out


def yearly_returns(equity: pd.Series) -> dict[str, float]:
    rets = _period_returns(equity, "YE")
    return {str(t.year): round(float(v) * 100, 2) for t, v in rets.items()}


def trade_stats(trades: Sequence[ClosedTrade]) -> dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "payoff_ratio": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "avg_trade_return_pct": 0.0,
            "avg_holding_hours": 0.0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
            "longs": 0,
            "shorts": 0,
        }
    pnl = np.array([t.pnl for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    gross_win, gross_loss = wins.sum(), -losses.sum()
    streak_w = streak_l = best_w = best_l = 0
    for p in pnl:
        if p > 0:
            streak_w += 1
            streak_l = 0
        else:
            streak_l += 1
            streak_w = 0
        best_w, best_l = max(best_w, streak_w), max(best_l, streak_l)
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    return {
        "total_trades": n,
        "win_rate": round(float((pnl > 0).mean() * 100), 2),
        "profit_factor": round(float(gross_win / gross_loss), 3)
        if gross_loss > 0
        else float("inf")
        if gross_win > 0
        else 0.0,
        "expectancy": round(float(pnl.mean()), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(avg_win / abs(avg_loss), 3) if avg_loss else 0.0,
        "largest_win": round(float(pnl.max()), 2),
        "largest_loss": round(float(pnl.min()), 2),
        "avg_trade_return_pct": round(float(np.mean([t.return_pct for t in trades])), 3),
        "avg_holding_hours": round(float(np.mean([t.holding_ms for t in trades]) / 3.6e6), 2),
        "max_consecutive_wins": int(best_w),
        "max_consecutive_losses": int(best_l),
        "longs": sum(1 for t in trades if t.side == "long"),
        "shorts": sum(1 for t in trades if t.side == "short"),
    }


def compute_metrics(
    equity: pd.Series,
    trades: Sequence[ClosedTrade] = (),
    *,
    exposure: pd.Series | None = None,
    benchmark: pd.Series | None = None,
    fees_paid: float = 0.0,
    funding_paid: float = 0.0,
    n_trials: int = 1,
    trial_sharpes: Sequence[float] | None = None,
    periods_per_year: float = 365.0,
) -> dict[str, Any]:
    """Full metric set from an equity curve (UTC DatetimeIndex) and closed trades."""
    equity = equity.dropna()
    if len(equity) < 2:
        return {"error": "not enough data"}
    start_eq, end_eq = float(equity.iloc[0]), float(equity.iloc[-1])
    rets = daily_returns(equity)
    days = (equity.index[-1] - equity.index[0]).total_seconds() / 86400.0
    years = max(days / 365.0, 1e-9)
    total_ret = end_eq / start_eq - 1.0
    cagr = (end_eq / start_eq) ** (1.0 / years) - 1.0 if end_eq > 0 else -1.0
    dd = drawdown_series(equity)
    max_dd = float(dd.min())
    vol = float(rets.std(ddof=1) * math.sqrt(periods_per_year)) if len(rets) > 1 else 0.0
    sh = sharpe(rets.values, periods_per_year)
    m: dict[str, Any] = {
        "start": equity.index[0].isoformat(),
        "end": equity.index[-1].isoformat(),
        "days": round(days, 1),
        "starting_balance": round(start_eq, 2),
        "final_equity": round(end_eq, 2),
        "net_profit": round(end_eq - start_eq, 2),
        "total_return_pct": round(total_ret * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "volatility_pct": round(vol * 100, 2),
        "sharpe": round(sh, 3),
        "sortino": round(sortino(rets.values, periods_per_year), 3),
        "calmar": round(cagr / abs(max_dd), 3) if max_dd < 0 else 0.0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration_days": round(max_drawdown_duration_days(equity), 1),
        "skew": round(float(stats.skew(rets.values, bias=False)), 3) if len(rets) > 3 else 0.0,
        "kurtosis": round(float(stats.kurtosis(rets.values, fisher=False, bias=False)), 3)
        if len(rets) > 3
        else 0.0,
        "best_day_pct": round(float(rets.max() * 100), 2) if len(rets) else 0.0,
        "worst_day_pct": round(float(rets.min() * 100), 2) if len(rets) else 0.0,
        "psr": round(probabilistic_sharpe_ratio(rets.values), 4),
        "dsr": round(deflated_sharpe_ratio(rets.values, n_trials, trial_sharpes), 4),
        "n_trials": int(n_trials),
        "fees_paid": round(fees_paid, 2),
        "funding_paid": round(funding_paid, 2),
    }
    if exposure is not None and len(exposure):
        m["exposure_pct"] = round(float((exposure > 1e-9).mean() * 100), 2)
        m["avg_gross_exposure"] = round(float(exposure.mean()), 3)
    m.update(trade_stats(trades))
    m["monthly_returns"] = monthly_returns(equity)
    m["yearly_returns"] = yearly_returns(equity)
    if benchmark is not None and len(benchmark.dropna()) > 2:
        b = benchmark.dropna()
        b = b[(b.index >= equity.index[0]) & (b.index <= equity.index[-1])]
        if len(b) > 2:
            b_eq = b / b.iloc[0] * start_eq
            b_rets = daily_returns(b_eq)
            joined = pd.concat([rets, b_rets], axis=1, keys=["s", "b"]).dropna()
            b_years = max((b.index[-1] - b.index[0]).total_seconds() / 86400.0 / 365.0, 1e-9)
            has_var = joined["b"].var() > 0 and joined["s"].var() > 0
            beta = float(np.cov(joined["s"], joined["b"])[0, 1] / joined["b"].var()) if has_var else 0.0
            m["benchmark"] = {
                "total_return_pct": round((float(b.iloc[-1] / b.iloc[0]) - 1) * 100, 2),
                "cagr_pct": round(((float(b.iloc[-1] / b.iloc[0])) ** (1 / b_years) - 1) * 100, 2),
                "sharpe": round(sharpe(b_rets.values, periods_per_year), 3),
                "max_drawdown_pct": round(float(drawdown_series(b_eq).min()) * 100, 2),
                "correlation": round(float(joined["s"].corr(joined["b"])), 3) if has_var else 0.0,
                "beta": round(beta, 3),
                "alpha_ann_pct": round(
                    float((joined["s"].mean() - beta * joined["b"].mean()) * periods_per_year * 100), 2
                ),
            }
    return m


def summary_row(m: dict[str, Any]) -> dict[str, Any]:
    """Compact subset for leaderboards."""
    keys = [
        "cagr_pct",
        "sharpe",
        "sortino",
        "calmar",
        "max_drawdown_pct",
        "volatility_pct",
        "total_trades",
        "win_rate",
        "profit_factor",
        "exposure_pct",
        "psr",
        "dsr",
    ]
    return {k: m.get(k) for k in keys}
