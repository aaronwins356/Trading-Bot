"""Look-ahead bias detection (inspired by Freqtrade's ``lookahead-analysis``).

Two independent checks:

1. **Indicator check** - ``precompute()`` is run on the full history and on
   truncated histories; any value at index ``i <= cut`` that changes when future
   candles are removed proves the indicator peeks ahead.
2. **Decision check** - the full backtest's entry decisions are replayed on data
   truncated right after each decision bar. If the strategy would not have made the
   same decision without the future candles, it is biased.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.candles import TS
from ..core.types import OrderRole
from ..strategy.base import Strategy
from ..strategy.portfolio import PortfolioStrategy
from .engine import BacktestConfig, run_backtest


@dataclass
class LookaheadReport:
    strategy: str
    biased_indicators: list[str] = field(default_factory=list)
    checked_indicators: list[str] = field(default_factory=list)
    decisions_checked: int = 0
    decisions_mismatched: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_bias(self) -> bool:
        return bool(self.biased_indicators) or self.decisions_mismatched > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "has_bias": self.has_bias,
            "biased_indicators": self.biased_indicators,
            "checked_indicators": self.checked_indicators,
            "decisions_checked": self.decisions_checked,
            "decisions_mismatched": self.decisions_mismatched,
            "details": self.details[:50],
        }


def _indicators(
    strategy_cls: type[Strategy], candles: np.ndarray, timeframe: str, hp: dict | None
) -> dict[str, np.ndarray]:
    st = strategy_cls()
    st.timeframe = timeframe
    defaults = {p["name"]: p.get("default") for p in st.hyperparameters()}
    st.hp = {**defaults, **(hp or {})}
    out = st.precompute(candles) or {}
    return {k: np.asarray(v, dtype="float64") for k, v in out.items()}


def check_lookahead(
    strategy_cls: type,
    candles: np.ndarray,
    timeframe: str,
    hp: dict | None = None,
    n_cuts: int = 12,
    max_decisions: int = 20,
    config: BacktestConfig | None = None,
    seed: int = 0,
) -> LookaheadReport:
    if issubclass(strategy_cls, PortfolioStrategy):
        raise TypeError("use check_portfolio_lookahead for portfolio strategies")
    report = LookaheadReport(strategy_cls.__name__)
    rng = np.random.default_rng(seed)
    n = len(candles)
    full = _indicators(strategy_cls, candles, timeframe, hp)
    report.checked_indicators = sorted(full)
    lo = min(max(50, n // 10), n - 1)
    cuts = sorted(set(rng.integers(lo, n - 1, size=n_cuts).tolist())) if n - 1 > lo else []
    biased: set[str] = set()
    for cut in cuts:
        part = _indicators(strategy_cls, candles[: cut + 1], timeframe, hp)
        for k, v in full.items():
            a, b = v[: cut + 1], part.get(k)
            if b is None or len(b) != cut + 1:
                continue
            both = ~np.isnan(a) & ~np.isnan(b)
            if (np.isnan(a) != np.isnan(b)).any() or not np.allclose(a[both], b[both], rtol=1e-7, atol=1e-9):
                biased.add(k)
                report.details.append({"type": "indicator", "name": k, "cut_index": int(cut)})
    report.biased_indicators = sorted(biased)

    cfg = config or BacktestConfig()
    base = run_backtest(strategy_cls, candles, timeframe, hp=hp, config=cfg, compute_stats=False)
    entries = [o for o in base.orders if o.role is OrderRole.ENTRY and o.status.value != "rejected"]
    if len(entries) > max_decisions:
        entries = [entries[i] for i in sorted(rng.choice(len(entries), max_decisions, replace=False))]
    ts = candles[:, TS].astype("int64")
    from ..core import timeframes as tfs

    tf_ms = tfs.to_ms(timeframe)
    for o in entries:
        # bar k that produced the order: decisions happen at its close (created_at == ts[k] + tf),
        # intrabar reactions strictly inside it (ts[k] < created_at < ts[k] + tf)
        k = int(np.searchsorted(ts, o.created_at - 1, side="right")) - 1
        if k < 0 or k >= n or not (ts[k] < o.created_at <= ts[k] + tf_ms):
            continue
        trunc = run_backtest(
            strategy_cls, candles[: k + 1], timeframe, hp=hp, config=cfg, compute_stats=False
        )
        same = any(
            x.role is OrderRole.ENTRY
            and x.created_at == o.created_at
            and x.side == o.side
            and x.type == o.type
            for x in trunc.orders
        )
        report.decisions_checked += 1
        if not same:
            report.decisions_mismatched += 1
            report.details.append({"type": "decision", "time": int(o.created_at), "side": str(o.side)})
    return report


def check_portfolio_lookahead(
    strategy_cls: type[PortfolioStrategy],
    data: dict[str, np.ndarray],
    timeframe: str,
    hp: dict | None = None,
    n_cuts: int = 6,
    seed: int = 0,
) -> LookaheadReport:
    """Indicator check for portfolio strategies (per symbol, truncated at common timestamps)."""
    report = LookaheadReport(strategy_cls.__name__)
    st = strategy_cls()
    st._bind(data, timeframe, hp)
    full = st._ind
    all_ts = np.unique(np.concatenate([d[:, TS] for d in data.values()]))
    rng = np.random.default_rng(seed)
    lo = max(50, len(all_ts) // 10)
    cuts = (
        sorted(set(rng.integers(lo, len(all_ts) - 1, size=n_cuts).tolist())) if len(all_ts) - 1 > lo else []
    )
    biased: set[str] = set()
    for cut in cuts:
        t_cut = all_ts[cut]
        part_data = {s: d[d[:, TS] <= t_cut] for s, d in data.items()}
        part_data = {s: d for s, d in part_data.items() if len(d)}
        st2 = strategy_cls()
        st2._bind(part_data, timeframe, hp)
        for s, inds in full.items():
            for k, v in inds.items():
                name = f"{s}:{k}"
                report.checked_indicators.append(name)
                b = st2._ind.get(s, {}).get(k)
                if b is None:
                    continue
                a = np.asarray(v)[: len(b)]
                both = ~np.isnan(a) & ~np.isnan(b)
                if not np.allclose(a[both], b[both], rtol=1e-7, atol=1e-9):
                    biased.add(name)
    report.checked_indicators = sorted(set(report.checked_indicators))
    report.biased_indicators = sorted(biased)
    return report
