"""Walk-forward analysis (Pardo): optimise on a rolling training window, trade the
chosen parameters on the following unseen window, roll forward, and stitch the
out-of-sample (OOS) segments into one equity curve."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .metrics import compute_metrics
from .optimize import best_trial, grid_search, robust_trial
from .runner import BacktestSpec, daily_equity


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    hp: dict[str, Any] = field(default_factory=dict)
    train_score: float = float("nan")
    test_metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        keep = ("cagr_pct", "sharpe", "max_drawdown_pct", "total_trades", "total_return_pct")
        return {
            "train_window": [self.train_start.date().isoformat(), self.train_end.date().isoformat()],
            "test_window": [self.test_start.date().isoformat(), self.test_end.date().isoformat()],
            "hp": self.hp,
            "train_score": None if not np.isfinite(self.train_score) else round(self.train_score, 3),
            "test_metrics": {k: self.test_metrics.get(k) for k in keep},
        }


@dataclass
class WalkForwardResult:
    folds: list[Fold]
    oos_equity: pd.Series
    oos_metrics: dict[str, Any]
    trials_tested: int

    def to_dict(self, max_points: int = 1000) -> dict[str, Any]:
        eq = self.oos_equity
        step = max(1, len(eq) // max_points)
        return {
            "folds": [f.to_dict() for f in self.folds],
            "oos_metrics": {k: v for k, v in self.oos_metrics.items() if k not in ("monthly_returns",)},
            "oos_equity": [
                [int(t.value // 1_000_000), round(float(v), 2)] for t, v in eq.iloc[::step].items()
            ],
            "trials_tested": self.trials_tested,
        }


def make_folds(
    start: str, end: str, train_years: float, test_years: float, anchored: bool = False
) -> list[Fold]:
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    train_m, test_m = round(train_years * 12), round(test_years * 12)
    folds = []
    k = 0
    while True:
        test_start = s + pd.DateOffset(months=train_m + k * test_m)
        if test_start >= e:
            break
        test_end = min(test_start + pd.DateOffset(months=test_m), e)
        train_start = s if anchored else test_start - pd.DateOffset(months=train_m)
        folds.append(Fold(train_start, test_start, test_start, test_end))
        k += 1
    return folds


def walk_forward(
    spec: BacktestSpec,
    grid: dict[str, Sequence[Any]],
    start: str,
    end: str,
    train_years: float = 3.0,
    test_years: float = 1.0,
    objective: str = "sharpe",
    selection: str = "robust",
    min_trades: int = 3,
    anchored: bool = False,
    n_jobs: int | None = None,
    base_hp: dict[str, Any] | None = None,
) -> WalkForwardResult:
    folds = make_folds(start, end, train_years, test_years, anchored)
    if not folds:
        raise ValueError("period too short for the requested walk-forward windows")
    pieces: list[pd.Series] = []
    tested = 0
    for f in folds:
        trials = grid_search(spec, grid, f.train_start, f.train_end, n_jobs=n_jobs, base_hp=base_hp)
        tested += len(trials)
        pick = (
            robust_trial(trials, grid, objective, min_trades)
            if selection == "robust"
            else best_trial(trials, objective, min_trades)
        )
        f.hp = pick.hp
        f.train_score = pick.score(objective, min_trades)
        res = spec.run(pick.hp, f.test_start, f.test_end)
        f.test_metrics = res.metrics
        pieces.append(daily_equity(res).pct_change().dropna())
    rets = pd.concat(pieces)
    rets = rets[~rets.index.duplicated(keep="first")].sort_index()
    eq = (1 + rets).cumprod() * spec.config.starting_balance
    first = pd.Series([spec.config.starting_balance], index=[rets.index[0] - pd.Timedelta(days=1)])
    eq = pd.concat([first, eq])
    return WalkForwardResult(folds, eq, compute_metrics(eq, n_trials=tested), tested)
