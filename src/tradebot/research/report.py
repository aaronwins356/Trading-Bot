"""Render docs/STRATEGIES.md from research/results (``tradebot research report``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VERDICT = {
    "recommended": "✅ Recommended",
    "conditional": "⚠️ Conditional",
    "not-recommended": "❌ Not recommended",
}


def _f(v: Any, digits: int = 2, pct: bool = False) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{x:.{digits}f}"
    return f"{s}%" if pct else s


def render(results_dir: str | Path = "research/results") -> str:
    rd = Path(results_dir)
    summary = json.loads((rd / "summary.json").read_text())
    studies = {r["key"]: json.loads((rd / f"{r['key']}.json").read_text()) for r in summary["studies"]}
    out: list[str] = []
    w = out.append
    w("# Strategy research results\n")
    w(
        f"_Generated {summary['generated_at'][:10]} by `tradebot research run` (then `tradebot research report`)._ "
        "Every number below is reproducible from the free datasets (`tradebot data free`).\n"
    )
    w("## Leaderboard\n")
    w(
        "Costs are included everywhere: BTC spot 0.10% fee + 5 bps slippage per side; altcoins 0.10% + 15 bps; "
        "perpetual futures 0.02%/0.05% maker/taker + 2-3 bps + 0.01% funding per 8h. "
        "**OOS** = walk-forward out-of-sample (parameters re-optimised on a rolling window, then traded blind). "
        "**DSR** = Deflated Sharpe Ratio (probability the Sharpe is real after accounting for every variant tried). "
        "**PBO** = Probability of Backtest Overfitting (lower is better).\n"
    )
    w(
        "| Strategy | Verdict | CAGR | Sharpe | Max DD | OOS Sharpe | OOS CAGR | OOS Max DD | DSR | PBO | Sharpe @2× costs | B&H Sharpe | B&H Max DD |"
    )
    w("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in summary["studies"]:
        w(
            f"| [{r['title']}](#{r['key'].replace('_', '-')}) | {VERDICT.get(r['verdict'], r['verdict'])} | "
            f"{_f(r['cagr_pct'], 1, True)} | {_f(r['sharpe'])} | {_f(r['max_drawdown_pct'], 1, True)} | "
            f"{_f(r['wf_oos_sharpe'])} | {_f(r['wf_oos_cagr_pct'], 1, True)} | {_f(r['wf_oos_max_drawdown_pct'], 1, True)} | "
            f"{_f(r['dsr'])} | {_f(r['pbo'])} | {_f(r['sharpe_2x_costs'])} | {_f(r['benchmark_sharpe'])} | "
            f"{_f(r['benchmark_max_drawdown_pct'], 1, True)} |"
        )
    w("")
    pf = summary.get("strategy_portfolio")
    if pf:
        m = pf["metrics"]
        w("## Portfolio of the recommended strategies\n")
        w(
            f"Blending one variant of each recommended strategy family ({', '.join(pf['members'])}) with "
            f"{pf['weights']}: **CAGR {_f(m['cagr_pct'], 1, True)}, Sharpe {_f(m['sharpe'])}, max drawdown "
            f"{_f(m['max_drawdown_pct'], 1, True)}**, volatility {_f(m['volatility_pct'], 1, True)}. "
            "This is an in-sample blend of full-period backtests - an illustration of diversification, "
            "not an out-of-sample result.\n"
        )
    w("## Methodology\n")
    w(
        "Each study runs: (1) a full-period backtest with *a-priori* parameters from the literature; "
        "(2) sub-period stability; (3) a parameter grid -> robustness share, heatmap, Deflated Sharpe Ratio; "
        "(4) PBO via combinatorially symmetric cross-validation; (5) walk-forward analysis with robust "
        "(plateau) parameter selection; (6) cost stress at 0/1/2/3x; (7) a 20-day block bootstrap; "
        "(8) a look-ahead bias check. A strategy is **recommended** only if it passes every gate:\n"
    )
    first = next(iter(studies.values()))
    for g in first["verdict_reasons"]:
        w(f"- {g[5:]}")
    w("")
    for key, s in studies.items():
        m = s["full"]["metrics"]
        b = m.get("benchmark") or {}
        wf = (s.get("walk_forward") or {}).get("oos_metrics") or {}
        w(f"## {s['title']}\n")
        w(
            f'<a id="{key.replace("_", "-")}"></a>**Verdict: {VERDICT.get(s["verdict"], s["verdict"])}** · strategy `{s["strategy"]}` · {s["timeframe"]} · parameters `{json.dumps(s["full"]["hp"])}`\n'
        )
        w(f"{s['thesis']}\n")
        w("| | Strategy | Buy & hold |")
        w("|---|---:|---:|")
        w(f"| CAGR | {_f(m['cagr_pct'], 1, True)} | {_f(b.get('cagr_pct'), 1, True)} |")
        w(f"| Sharpe | {_f(m['sharpe'])} | {_f(b.get('sharpe'))} |")
        w(
            f"| Max drawdown | {_f(m['max_drawdown_pct'], 1, True)} | {_f(b.get('max_drawdown_pct'), 1, True)} |"
        )
        w(f"| Sortino / Calmar | {_f(m['sortino'])} / {_f(m['calmar'])} | |")
        w(
            f"| Trades / win rate / profit factor | {m['total_trades']} / {_f(m['win_rate'], 1, True)} / {_f(m['profit_factor'])} | |"
        )
        w(f"| Time in market | {_f(m.get('exposure_pct'), 0, True)} | 100% |")
        if wf:
            w(
                f"| Walk-forward OOS CAGR / Sharpe / max DD | {_f(wf.get('cagr_pct'), 1, True)} / {_f(wf.get('sharpe'))} / {_f(wf.get('max_drawdown_pct'), 1, True)} | |"
            )
        w(f"| Deflated Sharpe / PBO | {_f(s.get('dsr'))} / {_f((s.get('pbo') or {}).get('pbo'))} | |")
        w("")
        if s.get("subperiods"):
            bsub = {p["period"]: p for p in s.get("benchmark_subperiods") or []}
            w("| Period | CAGR | Sharpe | Max DD | B&H Sharpe | B&H Max DD |")
            w("|---|---:|---:|---:|---:|---:|")
            for p in s["subperiods"]:
                bp = bsub.get(p["period"], {})
                w(
                    f"| {p['period']} | {_f(p['cagr_pct'], 1, True)} | {_f(p['sharpe'])} | {_f(p['max_drawdown_pct'], 1, True)} | {_f(bp.get('sharpe'))} | {_f(bp.get('max_drawdown_pct'), 1, True)} |"
                )
            w("")
        cs = s.get("cost_stress") or []
        if cs:
            w(
                "Cost sensitivity (Sharpe): "
                + ", ".join(f"{c['cost_multiplier']:g}× → {_f(c['sharpe'])}" for c in cs)
                + ".  "
            )
        bs = s.get("bootstrap") or {}
        if bs.get("cagr_pct"):
            w(
                f"Bootstrap ({round(bs['horizon_days'] / 365)}-year paths): CAGR 5th/50th/95th percentile "
                f"{_f(bs['cagr_pct']['p5'], 1, True)} / {_f(bs['cagr_pct']['p50'], 1, True)} / {_f(bs['cagr_pct']['p95'], 1, True)}; "
                f"P(losing first year) {_f((bs.get('prob_losing_year') or 0) * 100, 0, True)}.  "
            )
        la = s.get("lookahead") or {}
        w(
            f"Look-ahead check: {'**bias detected**' if la.get('has_bias') else 'clean'} ({len(la.get('checked_indicators', []))} indicators, {la.get('decisions_checked', 0)} decisions replayed).\n"
        )
        fails = [r[5:] for r in s["verdict_reasons"] if r.startswith("FAIL")]
        if fails:
            w("Failed gates: " + "; ".join(fails) + ".\n")
    w("---\n")
    w("_Historical simulations are not a promise of future returns. Nothing here is financial advice._\n")
    return "\n".join(out)


def write(results_dir: str | Path = "research/results", out: str | Path = "docs/STRATEGIES.md") -> Path:
    p = Path(out)
    p.write_text(render(results_dir))
    return p
