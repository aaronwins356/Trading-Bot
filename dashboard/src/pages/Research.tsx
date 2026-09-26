import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { api, type LeaderRow, type ResearchSummary } from "../api";
import { Heatmap, TimeSeriesChart } from "../components/charts";
import { ChartFrame } from "../components/ChartFrame";
import { DataTable } from "../components/DataTable";
import { Card, ErrorNote, PageHead, StatTile, VerdictBadge } from "../components/ui";
import { date, money, num, pct } from "../format";
import { useTokens } from "../theme";

const GATES = [
  "Full-period Sharpe > 0.8 and above buy & hold",
  "Walk-forward out-of-sample Sharpe > 0.7 (rolling re-optimisation)",
  "Deflated Sharpe Ratio > 0.90 (corrects for every variant tried)",
  "Probability of Backtest Overfitting < 0.5 (CSCV)",
  "≥ 60% of the parameter grid with Sharpe > 0.5 (plateau, not a peak)",
  "Sharpe > 0.5 with fees and slippage doubled",
  "Positive Sharpe in every sub-period, and > 0.3 since 2022 (edge still alive)",
  "Max drawdown better than −50% in-sample and out-of-sample",
  "No look-ahead bias (indicator + decision replay checks)",
];

export default function ResearchPage() {
  const nav = useNavigate();
  const tk = useTokens(["--series-1"]);
  const q = useQuery({ queryKey: ["research"], queryFn: () => api<ResearchSummary>("/api/research"), staleTime: 60_000 });
  const s = q.data;
  const rows = s?.studies ?? [];
  const rec = rows.filter((r) => r.verdict === "recommended");
  const bestOos = rows.reduce((m, r) => Math.max(m, r.wf_oos_sharpe ?? -9), -9);
  const pf = s?.strategy_portfolio;
  const pfSeries = useMemo(() => (pf ? [{ id: "pf", label: "Strategy portfolio", data: pf.equity, color: tk["--series-1"], kind: "area" as const }] : []), [pf, tk]);
  const corrKeys = pf ? Object.keys(pf.correlation) : [];
  return (
    <>
      <PageHead
        title="Strategy research"
        sub="Every strategy is run through the same gauntlet on real data (BTC 1-minute history since 2012 and ~43 coins including dead ones) with fees and slippage. Only strategies passing every gate are recommended."
      />
      <ErrorNote error={q.error} />
      {s && (
        <div className="stack">
          <div className="grid grid-4">
            <StatTile label="Studies" value={String(rows.length)} delta={`generated ${date(Date.parse(s.generated_at))}`} />
            <StatTile label="Recommended" value={String(rec.length)} delta={rec.map((r) => r.strategy).filter((v, i, a) => a.indexOf(v) === i).join(", ")} />
            <StatTile label="Best out-of-sample Sharpe" value={num(bestOos)} delta="walk-forward, never seen by the optimiser" />
            <StatTile label="Buy & hold BTC Sharpe" value={num(rows.find((r) => r.key.includes("btc_1d"))?.benchmark_sharpe)} delta={`max drawdown ${pct(rows.find((r) => r.key.includes("btc_1d"))?.benchmark_max_drawdown_pct)}`} />
          </div>

          <Card title="Leaderboard" sub="Click a study for equity curves, walk-forward folds, parameter heatmaps and robustness checks" flush>
            <DataTable<LeaderRow>
              rows={rows}
              rowKey={(r) => r.key}
              onRowClick={(r) => nav(`/research/${r.key}`)}
              columns={[
                { key: "title", label: "Study", render: (r) => <div className="wrap" style={{ maxWidth: 340 }}><b>{r.strategy}</b><div className="muted" style={{ fontSize: 12 }}>{r.title}</div></div> },
                { key: "verdict", label: "Verdict", render: (r) => <VerdictBadge verdict={r.verdict} />, sortValue: (r) => ({ recommended: 3, conditional: 2 }[r.verdict] ?? 1) },
                { key: "cagr_pct", label: "CAGR", align: "right", render: (r) => pct(r.cagr_pct) },
                { key: "sharpe", label: "Sharpe", align: "right", render: (r) => num(r.sharpe) },
                { key: "max_drawdown_pct", label: "Max DD", align: "right", render: (r) => pct(r.max_drawdown_pct) },
                { key: "wf_oos_sharpe", label: "OOS Sharpe", align: "right", render: (r) => <b>{num(r.wf_oos_sharpe)}</b> },
                { key: "wf_oos_cagr_pct", label: "OOS CAGR", align: "right", render: (r) => pct(r.wf_oos_cagr_pct) },
                { key: "wf_oos_max_drawdown_pct", label: "OOS DD", align: "right", render: (r) => pct(r.wf_oos_max_drawdown_pct) },
                { key: "dsr", label: "DSR", align: "right", render: (r) => num(r.dsr) },
                { key: "pbo", label: "PBO", align: "right", render: (r) => num(r.pbo) },
                { key: "sharpe_2x_costs", label: "Sharpe 2× cost", align: "right", render: (r) => num(r.sharpe_2x_costs) },
                { key: "benchmark_sharpe", label: "B&H Sharpe", align: "right", render: (r) => <span className="muted">{num(r.benchmark_sharpe)}</span> },
              ]}
              initialSort={{ key: "wf_oos_sharpe", dir: "desc" }}
            />
          </Card>

          {pf && (
            <div className="grid grid-3">
              <div className="span-2">
                <ChartFrame
                  title="Portfolio of the recommended strategies"
                  sub={`${pf.members.join(" + ")} · ${pf.weights}`}
                  table={{ columns: ["Date", "Equity"], rows: pf.equity.filter((_, i) => i % 5 === 0).map(([t, v]) => [date(t), money(v)]) }}
                  caption="In-sample blend of full-period backtests (one variant per strategy family) — diversification illustration, not an out-of-sample result."
                >
                  <TimeSeriesChart series={pfSeries} height={280} log format={(v) => money(v)} />
                </ChartFrame>
              </div>
              <Card title="Blend statistics">
                <dl className="kv">
                  <dt>CAGR</dt><dd>{pct(pf.metrics.cagr_pct)}</dd>
                  <dt>Sharpe</dt><dd>{num(pf.metrics.sharpe)}</dd>
                  <dt>Sortino</dt><dd>{num(pf.metrics.sortino)}</dd>
                  <dt>Max drawdown</dt><dd>{pct(pf.metrics.max_drawdown_pct)}</dd>
                  <dt>Volatility</dt><dd>{pct(pf.metrics.volatility_pct)}</dd>
                  <dt>Calmar</dt><dd>{num(pf.metrics.calmar)}</dd>
                </dl>
                <h3 className="mt" style={{ marginBottom: 6 }}>Daily-return correlation</h3>
                <Heatmap
                  rows={corrKeys.map((k) => k.split("_").slice(0, 2).join(" "))}
                  cols={corrKeys.map((_, i) => String(i + 1))}
                  values={corrKeys.map((a) => corrKeys.map((b) => pf.correlation[a][b]))}
                  mode="diverging"
                  domain={[-1, 1]}
                  format={(v) => v.toFixed(2)}
                />
              </Card>
            </div>
          )}

          <Card title="How a strategy earns “recommended”" sub="Hard gates, applied identically to every study">
            <ol className="gates" style={{ listStyle: "decimal", paddingLeft: 20 }}>
              {GATES.map((g) => <li key={g} style={{ display: "list-item" }}>{g}</li>)}
            </ol>
            <p className="secondary mt" style={{ fontSize: 13 }}>
              Costs: BTC spot 0.10% fee + 5 bps slippage per side; altcoins 0.10% + 15 bps; perpetual futures 0.02%/0.05% + funding 0.01% per 8h. Past performance does not
              guarantee future results — these are research findings, not financial advice.
            </p>
          </Card>
        </div>
      )}
    </>
  );
}
