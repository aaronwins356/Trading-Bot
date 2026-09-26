"""A single object describing "run strategy X on data Y with costs Z" - used by
optimization, walk-forward analysis, validation, the CLI and the API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..strategy.portfolio import PortfolioStrategy
from .engine import BacktestConfig, BacktestResult, run_backtest, run_portfolio_backtest


@dataclass
class BacktestSpec:
    strategy: type
    data: dict[str, np.ndarray]
    timeframe: str
    config: BacktestConfig = field(default_factory=BacktestConfig)
    vars: dict[str, Any] = field(default_factory=dict)  # injected into strategy.vars (e.g. universe)
    sub_candles: dict[str, np.ndarray] | None = None
    benchmark_symbol: str | None = None

    @property
    def is_portfolio(self) -> bool:
        return issubclass(self.strategy, PortfolioStrategy)

    def run(
        self,
        hp: dict[str, Any] | None = None,
        start: str | int | None = None,
        end: str | int | None = None,
        *,
        n_trials: int = 1,
        compute_stats: bool = True,
        config: BacktestConfig | None = None,
        progress: Callable[[float], None] | None = None,
    ) -> BacktestResult:
        cfg = config or self.config
        if self.is_portfolio:
            st = self.strategy()
            st.vars.update(self.vars)
            return run_portfolio_backtest(
                st,
                self.data,
                self.timeframe,
                config=cfg,
                hp=hp,
                start=start,
                end=end,
                benchmark_symbol=self.benchmark_symbol,
                n_trials=n_trials,
                compute_stats=compute_stats,
                progress=progress,
            )
        if self.vars:
            strat: Any = self.strategy
            base_vars = dict(self.vars)

            class _WithVars(strat):  # type: ignore[misc, valid-type]
                def __init__(self) -> None:
                    super().__init__()
                    self.name = strat.__name__
                    self.vars.update(base_vars)

            cls: type = _WithVars
        else:
            cls = self.strategy
        return run_backtest(
            cls,
            self.data,
            self.timeframe,
            config=cfg,
            hp=hp,
            start=start,
            end=end,
            sub_candles=self.sub_candles,
            n_trials=n_trials,
            compute_stats=compute_stats,
            progress=progress,
        )


def daily_equity(result: BacktestResult) -> pd.Series:
    from .metrics import daily_equity as _de

    return _de(result.equity)
