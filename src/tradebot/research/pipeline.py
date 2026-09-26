"""Reproducible strategy research pipeline.

``tradebot research run`` executes every study below and writes JSON results to
``research/results`` (consumed by the dashboard) plus ``docs/STRATEGIES.md``.

Each study goes through the same gauntlet:

1. Full-period backtest with *a-priori* parameters (from the literature, not tuned)
2. Sub-period stability (2015-17 / 2018-21 / 2022-26)
3. Parameter grid -> robustness share + heatmap, Deflated Sharpe Ratio over all trials
4. Probability of Backtest Overfitting (CSCV) over the grid
5. Walk-forward analysis: rolling optimisation, stitched out-of-sample equity
6. Cost stress (0x / 1x / 2x / 3x fees+slippage)
7. Block bootstrap confidence intervals
8. Look-ahead bias check

A strategy is **recommended** only if it passes all hard gates (see ``verdict``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..backtest.engine import BacktestConfig
from ..backtest.lookahead import check_lookahead, check_portfolio_lookahead
from ..backtest.metrics import compute_metrics, deflated_sharpe_ratio
from ..backtest.metrics import daily_equity as daily_eq
from ..backtest.optimize import expand_grid, grid_search, summarize_grid, trial_sharpes_per_period
from ..backtest.runner import BacktestSpec, daily_equity
from ..backtest.validation import (
    block_bootstrap,
    cost_stress,
    probability_of_backtest_overfitting,
    subperiod_table,
)
from ..backtest.walkforward import walk_forward
from ..core.candles import CLOSE, TS
from ..core.jsonutil import dumps as _dumps
from ..data.sources import load_market_caps
from ..data.store import DataStore
from ..jev.strategy import JEVStrategy
from ..strategies import (
    DCABot,
    DonchianBreakout,
    GridTrading,
    MomentumRotation,
    MultiAssetTrend,
    RSI2MeanReversion,
    TrendVolTarget,
    VolatilityBreakout,
)

log = logging.getLogger("tradebot.research")

SPOT = BacktestConfig(fee_maker=0.001, fee_taker=0.001, slippage_bps=5.0)
SPOT_ALTS = BacktestConfig(fee_maker=0.001, fee_taker=0.001, slippage_bps=15.0)
FUTURES_LOW_FEE = BacktestConfig(
    fee_maker=0.0002,
    fee_taker=0.0005,
    slippage_bps=2.0,
    exchange_type="futures",
    leverage=3.0,
    funding_rate_8h=0.0001,
)
FUTURES_2X = BacktestConfig(
    fee_maker=0.0002,
    fee_taker=0.0005,
    slippage_bps=3.0,
    exchange_type="futures",
    leverage=3.0,
    funding_rate_8h=0.0001,
)

SUBPERIODS = [("2015-01-01", "2018-01-01"), ("2018-01-01", "2022-01-01"), ("2022-01-01", "2027-01-01")]


@dataclass
class Study:
    key: str
    title: str
    strategy: type
    dataset: str  # btc_1h | btc_4h | btc_1d | alts_1d
    timeframe: str
    config: BacktestConfig
    grid: dict[str, list[Any]]
    heatmap: tuple[str, str] | None = None
    default_hp: dict[str, Any] = field(default_factory=dict)
    start: str = "2015-01-01"
    wf_train_years: float = 3.0
    wf_test_years: float = 1.0
    thesis: str = ""
    walk_forward: bool = True


STUDIES: list[Study] = [
    Study(
        "trend_voltarget_btc_1d",
        "TSMOM + volatility targeting - BTC daily (spot)",
        TrendVolTarget,
        "btc_1d",
        "1d",
        SPOT,
        {"base_lookback_days": [3, 5, 7, 10, 14], "target_vol": [0.3, 0.5, 0.7], "band": [0.05, 0.1, 0.2]},
        ("base_lookback_days", "target_vol"),
        thesis="Moskowitz-Ooi-Pedersen time-series momentum across 1w-32w horizons; exposure scaled to 50% vol.",
    ),
    Study(
        "trend_voltarget_btc_1d_futures",
        "TSMOM + volatility targeting - BTC daily (perp futures, up to 2x)",
        TrendVolTarget,
        "btc_1d",
        "1d",
        FUTURES_2X,
        {"base_lookback_days": [3, 5, 7, 10, 14], "target_vol": [0.5, 0.8, 1.0], "band": [0.05, 0.1, 0.2]},
        ("base_lookback_days", "target_vol"),
        default_hp={"target_vol": 0.8, "max_leverage": 2.0},
        thesis="Same signal, higher risk budget on perpetual futures; pays 0.01%/8h funding while long.",
    ),
    Study(
        "donchian_btc_4h",
        "Donchian breakout (Turtle) + trailing ATR stop - BTC 4h",
        DonchianBreakout,
        "btc_4h",
        "4h",
        SPOT,
        {"entry": [20, 40, 55, 80, 120], "exit": [10, 20, 40], "atr_stop": [0.0, 3.0]},
        ("entry", "exit"),
        thesis="Channel breakouts capture persistent crypto trends; ATR trailing stop bounds losses.",
    ),
    Study(
        "donchian_btc_1d",
        "Donchian breakout (Turtle) - BTC daily",
        DonchianBreakout,
        "btc_1d",
        "1d",
        SPOT,
        {"entry": [10, 20, 30, 55], "exit": [5, 10, 20], "atr_stop": [0.0, 3.0]},
        ("entry", "exit"),
        default_hp={"entry": 20, "exit": 10},
        thesis="Classic 20/10 Turtle system on daily bars.",
    ),
    Study(
        "multi_asset_trend_1d",
        "Diversified trend - top-20 coins by market cap (point-in-time)",
        MultiAssetTrend,
        "alts_1d",
        "1d",
        SPOT_ALTS,
        {"base_lookback_days": [3, 5, 7, 10], "risk_budget": [0.5, 0.8, 1.2], "rebalance_bars": [1, 3, 7]},
        ("base_lookback_days", "risk_budget"),
        start="2017-01-01",
        wf_train_years=2.0,
        thesis="Trend following is the most robust anomaly across assets; diversifying across coins cuts drawdowns.",
    ),
    Study(
        "momentum_rotation_1d",
        "Cross-sectional momentum rotation + BTC regime filter - top-20 coins",
        MomentumRotation,
        "alts_1d",
        "1d",
        SPOT_ALTS,
        {"lookback_days": [14, 28, 56], "top_k": [3, 5], "regime_ma_days": [50, 100, 200]},
        ("lookback_days", "regime_ma_days"),
        start="2017-01-01",
        wf_train_years=2.0,
        thesis="Liu-Tsyvinski-Wu (2022): momentum prices the crypto cross-section. Regime filter avoids bear markets.",
    ),
    Study(
        "vol_breakout_btc_1h_futures",
        "Volatility breakout - BTC 1h on low-fee perp futures",
        VolatilityBreakout,
        "btc_1h",
        "1h",
        FUTURES_LOW_FEE,
        {"k": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8], "trend_filter_days": [0, 10, 20]},
        ("k", "trend_filter_days"),
        thesis="Larry Williams breakout: intraday range expansion continues. High turnover -> needs low fees.",
    ),
    Study(
        "vol_breakout_btc_1h_spot",
        "Volatility breakout - BTC 1h on spot (0.10% fees)",
        VolatilityBreakout,
        "btc_1h",
        "1h",
        SPOT,
        {"k": [0.3, 0.4, 0.5, 0.6, 0.7, 0.8], "trend_filter_days": [0, 10, 20]},
        ("k", "trend_filter_days"),
        thesis="Same rules at retail spot fees - the cost sensitivity control.",
    ),
    Study(
        "jev_consensus_btc_1d",
        "JEV quant consensus (LLM off) - BTC daily",
        JEVStrategy,
        "btc_1d",
        "1d",
        SPOT,
        {"target_vol": [0.3, 0.5, 0.7], "band": [0.05, 0.1, 0.2]},
        ("target_vol", "band"),
        thesis="The deterministic analyst-panel baseline that JEV's LLM may confirm or reduce (veto mode). "
        "The LLM layer itself can only be judged fairly in forward paper trading (models memorise history).",
    ),
    Study(
        "rsi2_btc_1d",
        "RSI(2) mean reversion (Connors) - BTC daily [negative control]",
        RSI2MeanReversion,
        "btc_1d",
        "1d",
        SPOT,
        {"entry_rsi": [5, 10, 20], "trend_ma": [0, 100, 200], "exit_ma": [3, 5, 10]},
        ("entry_rsi", "trend_ma"),
        thesis="Works on equity indices; crypto trends at daily horizons, so we expect it to fail.",
    ),
    Study(
        "grid_btc_1h",
        "Grid bot - BTC 1h (spot)",
        GridTrading,
        "btc_1h",
        "1h",
        SPOT,
        {"spacing_pct": [0.5, 1.0, 2.0, 4.0], "levels": [5, 10, 20]},
        ("spacing_pct", "levels"),
        thesis="Industry-standard retail bot. Short volatility: earns in ranges, holds bags in trends.",
    ),
    Study(
        "dca_btc_1d",
        "DCA bot - weekly buys over one year, BTC daily",
        DCABot,
        "btc_1d",
        "1d",
        SPOT,
        {"interval_bars": [7], "slices": [52]},
        None,
        thesis="Capital deployment rule; equivalent to buy-and-hold after the deployment period.",
        walk_forward=False,
    ),
]


# ============================================================================ data


def load_datasets(
    store: DataStore | None = None, needed: set[str] | None = None
) -> dict[str, BacktestSpec | dict]:
    store = store or DataStore()
    out: dict[str, Any] = {}
    for tf in ("1h", "4h", "1d"):
        key = f"btc_{tf}"
        if needed is None or key in needed:
            out[key] = {"BTC/USD": store.load("bitstamp", "BTC/USD", tf)}
    if needed is None or "alts_1d" in needed:
        data: dict[str, np.ndarray] = {}
        for item in store.list():
            if item["exchange"] == "coinmetrics" and item["timeframe"] == "1d":
                data[item["symbol"]] = store.load("coinmetrics", item["symbol"], "1d")
        caps = load_market_caps(store)
        out["alts_1d"] = data
        out["alts_universe"] = point_in_time_universe(data, caps, top_n=20)
    return out


def point_in_time_universe(
    data: dict[str, np.ndarray], caps: pd.DataFrame, top_n: int = 20
) -> dict[str, np.ndarray]:
    """Top-N by market cap using the *previous* day's ranking (no look-ahead)."""
    closes = {}
    for s, c in data.items():
        idx = pd.to_datetime(c[:, TS].astype("int64"), unit="ms", utc=True)
        closes[s] = pd.Series(c[:, CLOSE], index=idx)
    px = pd.DataFrame(closes)
    caps = caps.reindex(px.index).where(px.notna())
    rank = caps.rank(axis=1, ascending=False).shift(1)
    uni = {}
    for s, c in data.items():
        idx = pd.to_datetime(c[:, TS].astype("int64"), unit="ms", utc=True)
        r = rank[s].reindex(idx) if s in rank else pd.Series(np.nan, index=idx)
        uni[s] = (r <= top_n).to_numpy()
    return uni


def make_spec(study: Study, datasets: dict[str, Any]) -> BacktestSpec:
    data = datasets[study.dataset]
    vars_: dict[str, Any] = {}
    if study.dataset == "alts_1d":
        vars_["universe"] = datasets["alts_universe"]
    return BacktestSpec(
        strategy=study.strategy,
        data=data,
        timeframe=study.timeframe,
        config=study.config,
        vars=vars_,
        benchmark_symbol="BTC/USD" if "BTC/USD" in data else None,
    )


# ============================================================================ study


def _series_payload(s: pd.Series, max_points: int = 800, scale: float = 1.0) -> list[list[float]]:
    step = max(1, math.ceil(len(s) / max_points))
    t = s.iloc[::step]
    if len(s) and t.index[-1] != s.index[-1]:
        t = pd.concat([t, s.iloc[-1:]])
    return [[int(i.value // 1_000_000), round(float(v) * scale, 4)] for i, v in t.items() if np.isfinite(v)]


def verdict(r: dict[str, Any]) -> tuple[str, list[str]]:
    """Hard gates for a 'recommended' strategy. Returns (verdict, reasons)."""
    reasons, fails = [], 0
    full = r["full"]["metrics"]
    wf = (r.get("walk_forward") or {}).get("oos_metrics", {})
    stress = {c["cost_multiplier"]: c for c in r.get("cost_stress", [])}
    grid = r.get("grid", {}).get("summary", {})
    pbo = (r.get("pbo") or {}).get("pbo")
    checks = [
        ("full-period Sharpe > benchmark-adjusted floor 0.8", full.get("sharpe", 0) > 0.8),
        (
            "beats buy & hold on Sharpe",
            full.get("sharpe", 0) > (full.get("benchmark") or {}).get("sharpe", 0),
        ),
        ("walk-forward OOS Sharpe > 0.7", (wf.get("sharpe") or 0) > 0.7 if wf else False),
        ("Deflated Sharpe Ratio > 0.90", r.get("dsr", 0) > 0.9),
        ("Sharpe > 0.5 at 2x costs", (stress.get(2.0, {}).get("sharpe") or 0) > 0.5),
        (">= 60% of parameter grid with Sharpe > 0.5", (grid.get("share_above_0_5") or 0) >= 0.6),
        ("PBO < 0.5", pbo is not None and pbo < 0.5),
        (
            "positive Sharpe in every sub-period",
            all((p.get("sharpe") or 0) > 0 for p in r.get("subperiods", [])),
        ),
        (
            "edge still alive: Sharpe > 0.3 since 2022",
            ((r.get("subperiods") or [{}])[-1].get("sharpe") or 0) > 0.3,
        ),
        ("max drawdown better than -50%", (full.get("max_drawdown_pct") or -100) > -50),
        (
            "walk-forward OOS max drawdown better than -50%",
            (wf.get("max_drawdown_pct") or -100) > -50 if wf else False,
        ),
        ("no look-ahead bias detected", not (r.get("lookahead") or {}).get("has_bias", False)),
    ]
    for name, ok in checks:
        reasons.append(("PASS " if ok else "FAIL ") + name)
        fails += 0 if ok else 1
    if fails == 0:
        return "recommended", reasons
    core_ok = checks[2][1] and checks[3][1] and checks[-1][1] and full.get("sharpe", 0) > 0.8
    if core_ok and fails <= 3:
        return "conditional", reasons
    return "not-recommended", reasons


def run_study(
    study: Study, datasets: dict[str, Any], fast: bool = False, n_jobs: int | None = None
) -> dict[str, Any]:
    t0 = time.perf_counter()
    spec = make_spec(study, datasets)
    log.info("[%s] full-period backtest", study.key)
    grid_size = len(expand_grid(study.grid))
    full = spec.run(study.default_hp or None, study.start, n_trials=max(grid_size, 1))
    d_eq = daily_equity(full)
    d_ret = d_eq.pct_change().dropna()
    out: dict[str, Any] = {
        "key": study.key,
        "title": study.title,
        "strategy": study.strategy.__name__,
        "dataset": study.dataset,
        "timeframe": study.timeframe,
        "thesis": study.thesis,
        "config": {
            "fee_maker": study.config.fee_maker,
            "fee_taker": study.config.fee_taker,
            "slippage_bps": study.config.slippage_bps,
            "exchange_type": str(study.config.exchange_type),
            "funding_rate_8h": study.config.funding_rate_8h,
        },
        "full": {
            "hp": full.hp,
            "metrics": full.metrics,
            "equity": _series_payload(d_eq),
            "benchmark": _series_payload(daily_eq(full.benchmark)) if full.benchmark is not None else None,
            "drawdown": _series_payload(d_eq / d_eq.cummax() - 1.0, scale=100),
            # full-resolution daily equity (for blending strategies; charts use the down-sampled series)
            "daily_equity": {
                "start": int(d_eq.index[0].value // 1_000_000),
                "values": [round(float(v), 2) for v in d_eq.values],
            },
            "trades": [t.to_dict() for t in full.trades[-300:]],
        },
        "subperiods": subperiod_table(d_ret, SUBPERIODS),
    }
    if full.benchmark is not None:
        b = daily_eq(full.benchmark).pct_change().dropna()
        out["benchmark_subperiods"] = subperiod_table(b, SUBPERIODS)

    # ---- parameter grid: robustness, heatmap, DSR, PBO
    log.info("[%s] parameter grid (%d configs)", study.key, grid_size)
    trials = (
        grid_search(spec, study.grid, study.start, None, n_jobs=n_jobs, base_hp=study.default_hp)
        if grid_size > 1
        else []
    )
    if trials:
        out["grid"] = {
            "summary": summarize_grid(trials),
            "trials": [
                {
                    "hp": t.hp,
                    "sharpe": t.metrics.get("sharpe"),
                    "cagr_pct": t.metrics.get("cagr_pct"),
                    "max_drawdown_pct": t.metrics.get("max_drawdown_pct"),
                    "total_trades": t.metrics.get("total_trades"),
                }
                for t in trials
            ],
        }
        if study.heatmap:
            out["grid"]["heatmap"] = _heatmap(trials, study.grid, study.heatmap, full.hp)
        sr_trials = trial_sharpes_per_period(trials)
        out["dsr"] = round(deflated_sharpe_ratio(d_ret.values, len(trials), sr_trials), 4)
        mat = pd.DataFrame({i: t.daily_returns for i, t in enumerate(trials) if len(t.daily_returns)})
        out["pbo"] = probability_of_backtest_overfitting(mat, n_splits=10 if fast else 16)
    else:
        out["grid"] = {"summary": {}}
        out["dsr"] = round(deflated_sharpe_ratio(d_ret.values, 1), 4)
        out["pbo"] = None

    # ---- walk-forward
    if study.walk_forward and grid_size > 1:
        log.info("[%s] walk-forward", study.key)
        end = pd.Timestamp(int(max(d[-1, TS] for d in spec.data.values())), unit="ms", tz="UTC").strftime(
            "%Y-%m-%d"
        )
        wf = walk_forward(
            spec,
            study.grid,
            study.start,
            end,
            train_years=study.wf_train_years,
            test_years=study.wf_test_years,
            selection="robust",
            n_jobs=n_jobs,
            base_hp=study.default_hp,
        )
        out["walk_forward"] = wf.to_dict()
    else:
        out["walk_forward"] = None

    # ---- costs, bootstrap, look-ahead
    log.info("[%s] cost stress / bootstrap / look-ahead", study.key)
    out["cost_stress"] = cost_stress(spec, full.hp, study.start, None, multipliers=(0.0, 1.0, 2.0, 3.0))
    out["bootstrap"] = block_bootstrap(d_ret, n_sims=500 if fast else 2000, block=20, horizon_days=365 * 3)
    if spec.is_portfolio:
        la = check_portfolio_lookahead(
            study.strategy, spec.data, study.timeframe, full.hp, n_cuts=3 if fast else 6
        )
    else:
        sym = next(iter(spec.data))
        candles = spec.data[sym]
        # a representative 3-year window keeps the replay cheap
        start_ms = int(pd.Timestamp("2019-01-01", tz="UTC").value // 1_000_000)
        window = candles[candles[:, TS] >= start_ms - 400 * (candles[1, TS] - candles[0, TS])]
        window = window[: min(len(window), 6000)]
        la = check_lookahead(
            study.strategy,
            window,
            study.timeframe,
            full.hp,
            n_cuts=6 if fast else 12,
            max_decisions=5 if fast else 15,
            config=study.config,
        )
    out["lookahead"] = la.to_dict()
    v, reasons = verdict(out)
    out["verdict"] = v
    out["verdict_reasons"] = reasons
    out["runtime_sec"] = round(time.perf_counter() - t0, 1)
    return out


def _heatmap(trials, grid, axes, default_hp) -> dict[str, Any]:
    x, y = axes
    fixed = {
        k: (default_hp.get(k) if default_hp.get(k) in grid[k] else grid[k][len(grid[k]) // 2])
        for k in grid
        if k not in axes
    }
    z = []
    for yv in grid[y]:
        row = []
        for xv in grid[x]:
            match = [
                t
                for t in trials
                if t.hp[x] == xv and t.hp[y] == yv and all(t.hp[k] == v for k, v in fixed.items())
            ]
            row.append(match[0].metrics.get("sharpe") if match else None)
        z.append(row)
    return {"x": x, "y": y, "x_values": grid[x], "y_values": grid[y], "fixed": fixed, "sharpe": z}


# ============================================================================ ensemble


def strategy_portfolio(results: list[dict[str, Any]], keys: list[str]) -> dict[str, Any] | None:
    """Equal-risk blend of the recommended strategies' daily returns (a 'portfolio of strategies')."""
    series = {}
    for r in results:
        if r["key"] in keys and r["full"].get("daily_equity"):
            de = r["full"]["daily_equity"]
            idx = pd.date_range(
                pd.Timestamp(de["start"], unit="ms", tz="UTC"), periods=len(de["values"]), freq="1D"
            )
            series[r["key"]] = pd.Series(de["values"], index=idx).pct_change()
    if len(series) < 2:
        return None
    df = pd.DataFrame(series).dropna()
    vol = df.rolling(90, min_periods=30).std().shift(1)
    w = (1 / vol).div((1 / vol).sum(axis=1), axis=0).fillna(1 / df.shape[1])
    blend = (df * w).sum(axis=1)
    eq = (1 + blend).cumprod() * 10_000
    m = compute_metrics(eq)
    return {
        "members": list(series),
        "weights": "inverse 90-day volatility, rebalanced daily",
        "metrics": {k: v for k, v in m.items() if k not in ("monthly_returns",)},
        "equity": _series_payload(eq),
        "correlation": df.corr().round(3).to_dict(),
    }


# ============================================================================ driver


def run_all(
    out_dir: str | Path = "research/results",
    only: list[str] | None = None,
    fast: bool = False,
    n_jobs: int | None = None,
    store: DataStore | None = None,
    on_study: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    studies = [s for s in STUDIES if not only or s.key in only]
    datasets = load_datasets(store, {s.dataset for s in studies})
    results = []
    for s in studies:
        r = run_study(s, datasets, fast=fast, n_jobs=n_jobs)
        (out / f"{s.key}.json").write_text(dumps(r))
        results.append(r)
        if on_study:
            on_study(r)
    # merge with previously computed studies so partial runs keep the leaderboard complete
    all_results = {r["key"]: r for r in results}
    for f in out.glob("*.json"):
        if f.stem not in all_results and f.stem not in ("summary",):
            with contextlib.suppress(json.JSONDecodeError):
                all_results[f.stem] = json.loads(f.read_text())
    merged = [all_results[s.key] for s in STUDIES if s.key in all_results]
    for r in merged:  # re-apply the current gates to cached results
        r["verdict"], r["verdict_reasons"] = verdict(r)
    # one variant per strategy family (the spot and 2x-futures TSMOM share a signal)
    # JEVStrategy trades the same trend analysts as TrendVolTarget (corr ~0.96): not a separate diversifier
    recommended, seen = [], {"JEVStrategy"}
    for r in merged:
        if r["verdict"] == "recommended" and r["strategy"] not in seen:
            recommended.append(r["key"])
            seen.add(r["strategy"])
    summary = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "studies": [_leaderboard_row(r) for r in merged],
        "strategy_portfolio": strategy_portfolio(merged, recommended),
        "methodology": __doc__,
    }
    (out / "summary.json").write_text(dumps(summary, indent=1))
    return summary


def _leaderboard_row(r: dict[str, Any]) -> dict[str, Any]:
    m = r["full"]["metrics"]
    wf = (r.get("walk_forward") or {}).get("oos_metrics") or {}
    stress = {c["cost_multiplier"]: c for c in r.get("cost_stress", [])}
    b = m.get("benchmark") or {}
    return {
        "key": r["key"],
        "title": r["title"],
        "strategy": r["strategy"],
        "timeframe": r["timeframe"],
        "verdict": r["verdict"],
        "cagr_pct": m.get("cagr_pct"),
        "sharpe": m.get("sharpe"),
        "sortino": m.get("sortino"),
        "max_drawdown_pct": m.get("max_drawdown_pct"),
        "calmar": m.get("calmar"),
        "total_trades": m.get("total_trades"),
        "exposure_pct": m.get("exposure_pct"),
        "wf_oos_sharpe": wf.get("sharpe"),
        "wf_oos_cagr_pct": wf.get("cagr_pct"),
        "wf_oos_max_drawdown_pct": wf.get("max_drawdown_pct"),
        "dsr": r.get("dsr"),
        "pbo": (r.get("pbo") or {}).get("pbo"),
        "sharpe_2x_costs": stress.get(2.0, {}).get("sharpe"),
        "grid_share_sharpe_gt_0_5": (r.get("grid", {}).get("summary") or {}).get("share_above_0_5"),
        "benchmark_sharpe": b.get("sharpe"),
        "benchmark_cagr_pct": b.get("cagr_pct"),
        "benchmark_max_drawdown_pct": b.get("max_drawdown_pct"),
        "start": m.get("start"),
        "end": m.get("end"),
    }


def dumps(o: Any, indent: int | None = None) -> str:
    return _dumps(o, indent=indent)


def _json_default(o: Any) -> Any:
    if isinstance(o, np.integer | np.floating):
        return o.item()
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, type):
        return o.__name__
    return str(o)
