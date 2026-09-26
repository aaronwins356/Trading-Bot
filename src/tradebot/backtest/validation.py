"""Robustness checks beyond a single backtest.

* Stationary block bootstrap of daily returns -> confidence intervals for CAGR,
  Sharpe, max drawdown and the probability of a losing year.
* Cost stress test (fees and slippage x2, x3).
* Probability of Backtest Overfitting via Combinatorially Symmetric Cross-Validation
  (Bailey, Borwein, López de Prado & Zhu, 2017).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np
import pandas as pd

from .engine import BacktestConfig
from .runner import BacktestSpec


def _max_dd(path: np.ndarray) -> float:
    peak = np.maximum.accumulate(path)
    return float((path / peak - 1.0).min())


def block_bootstrap(
    daily_returns: pd.Series | np.ndarray,
    n_sims: int = 2000,
    block: int = 20,
    horizon_days: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    """Circular block bootstrap (fixed blocks) preserving volatility clustering. Vectorized."""
    r = np.asarray(daily_returns, dtype="float64")
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return {"error": "not enough data"}
    h = horizon_days or n
    rng = np.random.default_rng(seed)
    n_blocks = math.ceil(h / block)
    starts = rng.integers(0, n, size=(n_sims, n_blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_sims, -1)[:, :h] % n
    sims = r[idx]
    paths = np.cumprod(1.0 + sims, axis=1)
    years = h / 365.0
    final = paths[:, -1]
    cagr = np.where(final > 0, np.power(np.maximum(final, 1e-12), 1.0 / years) - 1.0, -1.0)
    sd = sims.std(axis=1, ddof=1)
    sharpes = np.where(sd > 0, sims.mean(axis=1) / np.where(sd > 0, sd, 1) * math.sqrt(365), 0.0)
    dds = (paths / np.maximum.accumulate(paths, axis=1) - 1.0).min(axis=1)
    loss_year = (np.cumprod(1.0 + sims[:, :365], axis=1)[:, -1] < 1.0) if h >= 365 else None

    def q(a: np.ndarray, x: float, scale: float = 100.0) -> float:
        return round(float(np.percentile(a, x)) * scale, 2)

    return {
        "n_sims": n_sims,
        "block_days": block,
        "horizon_days": h,
        "cagr_pct": {"p5": q(cagr, 5), "p50": q(cagr, 50), "p95": q(cagr, 95)},
        "sharpe": {"p5": q(sharpes, 5, 1), "p50": q(sharpes, 50, 1), "p95": q(sharpes, 95, 1)},
        "max_drawdown_pct": {"p5": q(dds, 5), "p50": q(dds, 50), "p95": q(dds, 95)},
        "prob_losing_year": round(float(loss_year.mean()), 3) if loss_year is not None else None,
        "prob_sharpe_below_0": round(float((sharpes < 0).mean()), 3),
    }


def cost_stress(
    spec: BacktestSpec,
    hp: dict[str, Any] | None,
    start: Any = None,
    end: Any = None,
    multipliers: Sequence[float] = (0.0, 1.0, 2.0, 3.0),
) -> list[dict[str, Any]]:
    out = []
    base: BacktestConfig = spec.config
    for m in multipliers:
        cfg = replace(
            base,
            fee_maker=base.fee_maker * m,
            fee_taker=base.fee_taker * m,
            slippage_bps=base.slippage_bps * m,
        )
        res = spec.run(hp, start, end, config=cfg)
        out.append(
            {
                "cost_multiplier": m,
                "cagr_pct": res.metrics.get("cagr_pct"),
                "sharpe": res.metrics.get("sharpe"),
                "max_drawdown_pct": res.metrics.get("max_drawdown_pct"),
                "fees_paid": res.metrics.get("fees_paid"),
            }
        )
    return out


def probability_of_backtest_overfitting(
    returns: pd.DataFrame, n_splits: int = 16, max_combinations: int = 5000, seed: int = 0
) -> dict[str, Any]:
    """PBO via CSCV.

    ``returns``: T x N matrix of per-period returns of N strategy configurations.
    Rows are split into ``n_splits`` contiguous blocks; for every way of choosing
    half of the blocks as in-sample, the best IS configuration's OOS rank is
    recorded. PBO = share of splits where the IS winner lands below the OOS median.
    """
    m = returns.dropna(how="all").fillna(0.0).to_numpy()
    t, n = m.shape
    if n < 2 or t < n_splits * 5:
        return {"error": "need >= 2 configurations and enough observations"}
    n_splits -= n_splits % 2
    blocks = np.array_split(np.arange(t), n_splits)
    combos = list(itertools.combinations(range(n_splits), n_splits // 2))
    rng = np.random.default_rng(seed)
    if len(combos) > max_combinations:
        sel = rng.choice(len(combos), max_combinations, replace=False)
        combos = [combos[i] for i in sel]

    def sr(x: np.ndarray) -> np.ndarray:
        sd = x.std(axis=0, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(sd > 0, x.mean(axis=0) / sd, 0.0)

    logits, is_best_oos = [], []
    for c in combos:
        is_idx = np.concatenate([blocks[i] for i in c])
        oos_idx = np.concatenate([blocks[i] for i in range(n_splits) if i not in c])
        is_sr, oos_sr = sr(m[is_idx]), sr(m[oos_idx])
        best = int(np.argmax(is_sr))
        rank = (oos_sr < oos_sr[best]).sum() + 0.5 * ((oos_sr == oos_sr[best]).sum() - 1)
        w = (rank + 1) / (n + 1)  # relative rank in (0, 1)
        logits.append(math.log(w / (1 - w)))
        is_best_oos.append((float(is_sr[best]), float(oos_sr[best])))
    logits_a = np.asarray(logits)
    ib = np.asarray(is_best_oos)
    slope = float(np.polyfit(ib[:, 0], ib[:, 1], 1)[0]) if len(ib) > 2 else float("nan")
    return {
        "pbo": round(float((logits_a <= 0).mean()), 3),
        "n_configurations": n,
        "n_splits": n_splits,
        "n_combinations": len(combos),
        "logit_median": round(float(np.median(logits_a)), 3),
        "oos_vs_is_slope": round(slope, 3),
        "oos_sharpe_of_is_best_median": round(float(np.median(ib[:, 1]) * math.sqrt(365)), 3),
    }


def subperiod_table(daily_returns: pd.Series, periods: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    out = []
    for a, b in periods:
        r = daily_returns[
            (daily_returns.index >= pd.Timestamp(a, tz="UTC"))
            & (daily_returns.index < pd.Timestamp(b, tz="UTC"))
        ]
        if len(r) < 20:
            continue
        eq = np.cumprod(1 + r.values)
        yrs = len(r) / 365
        out.append(
            {
                "period": f"{a[:4]}-{b[:4]}",
                "cagr_pct": round((eq[-1] ** (1 / yrs) - 1) * 100, 2),
                "sharpe": round(float(r.mean() / r.std() * math.sqrt(365)) if r.std() > 0 else 0.0, 2),
                "max_drawdown_pct": round(_max_dd(eq) * 100, 2),
            }
        )
    return out
