"""Hyperparameter search: exhaustive grids (transparent, reproducible) and Optuna TPE.

Every configuration's daily returns are kept so that the multiple-testing
corrections (Deflated Sharpe Ratio, Probability of Backtest Overfitting) can be
computed over *all* variants tried - not just the winner.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .metrics import sharpe
from .runner import BacktestSpec, daily_equity

log = logging.getLogger("tradebot.optimize")

OBJECTIVES = ("sharpe", "sortino", "calmar", "cagr_pct", "profit_factor")


@dataclass
class TrialResult:
    hp: dict[str, Any]
    metrics: dict[str, Any]
    daily_returns: pd.Series

    def score(self, objective: str = "sharpe", min_trades: int = 0) -> float:
        if self.metrics.get("total_trades", 0) < min_trades:
            return -math.inf
        v = self.metrics.get(objective)
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return -math.inf
        return float(v)


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, vals, strict=True)) for vals in itertools.product(*(grid[k] for k in keys))]


_SPEC: BacktestSpec | None = None


def _init_worker(spec: BacktestSpec) -> None:
    global _SPEC
    _SPEC = spec


def _run_one(args: tuple[dict[str, Any], Any, Any]) -> TrialResult:
    hp, start, end = args
    assert _SPEC is not None
    return _trial(_SPEC, hp, start, end)


def _trial(spec: BacktestSpec, hp: dict[str, Any], start: Any, end: Any) -> TrialResult:
    try:
        res = spec.run(hp, start, end)
    except Exception as exc:  # invalid combination -> worst score
        log.debug("trial %s failed: %s", hp, exc)
        return TrialResult(hp, {"error": repr(exc), "total_trades": 0}, pd.Series(dtype=float))
    eq = daily_equity(res)
    m = {k: v for k, v in res.metrics.items() if k not in ("monthly_returns", "yearly_returns")}
    return TrialResult(hp, m, eq.pct_change().dropna())


def grid_search(
    spec: BacktestSpec,
    grid: dict[str, Sequence[Any]] | list[dict[str, Any]],
    start: Any = None,
    end: Any = None,
    n_jobs: int | None = None,
    base_hp: dict[str, Any] | None = None,
) -> list[TrialResult]:
    combos = expand_grid(grid) if isinstance(grid, dict) else list(grid)
    if base_hp:
        combos = [{**base_hp, **c} for c in combos]
    n_jobs = n_jobs if n_jobs is not None else min(len(combos), os.cpu_count() or 1)
    if n_jobs <= 1 or len(combos) <= 1:
        return [_trial(spec, hp, start, end) for hp in combos]
    with ProcessPoolExecutor(max_workers=n_jobs, initializer=_init_worker, initargs=(spec,)) as pool:
        return list(pool.map(_run_one, [(hp, start, end) for hp in combos], chunksize=1))


def best_trial(trials: Sequence[TrialResult], objective: str = "sharpe", min_trades: int = 0) -> TrialResult:
    return max(trials, key=lambda t: t.score(objective, min_trades))


def robust_trial(
    trials: Sequence[TrialResult],
    grid: dict[str, Sequence[Any]],
    objective: str = "sharpe",
    min_trades: int = 0,
) -> TrialResult:
    """Pick the configuration whose *neighbourhood* scores best (plateau, not peak).

    Each numeric parameter's neighbours are the adjacent grid values; the score of a
    configuration is the mean score over itself and all its neighbours. This favours
    broad, stable optima that are far more likely to persist out of sample.
    """
    keys = list(grid)
    index = {tuple(t.hp[k] for k in keys): t for t in trials}
    positions = {k: {v: i for i, v in enumerate(grid[k])} for k in keys}

    def neighbours(hp: dict[str, Any]) -> list[TrialResult]:
        out = []
        for k in keys:
            i = positions[k][hp[k]]
            for j in (i - 1, i + 1):
                if 0 <= j < len(grid[k]):
                    alt = dict(hp)
                    alt[k] = grid[k][j]
                    t = index.get(tuple(alt[q] for q in keys))
                    if t is not None:
                        out.append(t)
        return out

    def smooth(t: TrialResult) -> float:
        s = t.score(objective, min_trades)
        if not math.isfinite(s):
            return -math.inf
        vals = [s] + [n.score(objective, min_trades) for n in neighbours(t.hp)]
        vals = [v if math.isfinite(v) else -1.0 for v in vals]
        return float(np.mean(vals))

    return max(trials, key=smooth)


def optuna_search(
    spec: BacktestSpec,
    n_trials: int = 100,
    start: Any = None,
    end: Any = None,
    objective: str = "sharpe",
    min_trades: int = 5,
    seed: int = 42,
    space: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[TrialResult]]:
    """Bayesian optimisation (TPE) over the strategy's declared hyperparameters."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    params = space or spec.strategy().hyperparameters()
    results: list[TrialResult] = []

    def suggest(trial: Any) -> dict[str, Any]:
        hp: dict[str, Any] = {}
        for p in params:
            t = p.get("type")
            name = p["name"]
            if t in (int, "int"):
                hp[name] = trial.suggest_int(name, int(p["min"]), int(p["max"]), step=int(p.get("step", 1)))
            elif t in (float, "float"):
                hp[name] = trial.suggest_float(name, float(p["min"]), float(p["max"]))
            else:
                hp[name] = trial.suggest_categorical(name, list(p.get("options", [p.get("default")])))
        return hp

    def fn(trial: Any) -> float:
        tr = _trial(spec, suggest(trial), start, end)
        results.append(tr)
        s = tr.score(objective, min_trades)
        return s if math.isfinite(s) else -10.0

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(fn, n_trials=n_trials)
    return study.best_params, results


def trial_sharpes_per_period(trials: Sequence[TrialResult]) -> list[float]:
    """Per-period (daily, non-annualized) Sharpe of every trial - input for the DSR."""
    out = []
    for t in trials:
        r = t.daily_returns
        if len(r) > 2 and r.std() > 0:
            out.append(float(r.mean() / r.std()))
    return out


def summarize_grid(trials: Sequence[TrialResult], objective: str = "sharpe") -> dict[str, Any]:
    scores = np.array([t.metrics.get(objective, np.nan) for t in trials], dtype=float)
    scores = scores[np.isfinite(scores)]
    if not scores.size:
        return {"n": 0}
    return {
        "n": int(scores.size),
        "median": round(float(np.median(scores)), 3),
        "p10": round(float(np.percentile(scores, 10)), 3),
        "p90": round(float(np.percentile(scores, 90)), 3),
        "share_above_0": round(float((scores > 0).mean()), 3),
        "share_above_0_5": round(float((scores > 0.5).mean()), 3),
        "share_above_1": round(float((scores > 1.0).mean()), 3),
    }


def annualized_sharpe_of(returns: pd.Series) -> float:
    return sharpe(returns.values, 365.0)
