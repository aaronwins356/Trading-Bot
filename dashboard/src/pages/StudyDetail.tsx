import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Metrics, type Series, type Trade } from "../api";
import { BarChart, Heatmap, TimeSeriesChart } from "../components/charts";
import { ChartFrame, LegendItem } from "../components/ChartFrame";
import { DataTable } from "../components/DataTable";
import { Card, ErrorNote, Icon, PageHead, Segmented, StatTile, VerdictBadge } from "../components/ui";
import { date, dateTime, hours, money, num, pct, price, signedMoney, upDown } from "../format";
import { useTokens } from "../theme";

interface Study {
  key: string;
  title: string;
  strategy: string;
  timeframe: string;
  thesis: string;
  verdict: string;
  verdict_reasons: string[];
  config: Record<string, unknown>;
  full: { hp: Record<string, unknown>; metrics: Metrics; equity: Series; benchmark: Series | null; drawdown: Series; trades: Trade[] };
  subperiods: { period: string; cagr_pct: number; sharpe: number; max_drawdown_pct: number }[];
  benchmark_subperiods?: { period: string; cagr_pct: number; sharpe: number; max_drawdown_pct: number }[];
  grid: {
    summary: Record<string, number>;
    heatmap?: { x: string; y: string; x_values: number[]; y_values: number[]; fixed: Record<string, unknown>; sharpe: (number | null)[][] };
  };
  dsr: number;
  pbo: { pbo: number; n_configurations: number; n_combinations: number; oos_sharpe_of_is_best_median: number } | null;
  walk_forward: {
    folds: { train_window: string[]; test_window: string[]; hp: Record<string, unknown>; train_score: number | null; test_metrics: Record<string, number | null> }[];
    oos_metrics: Metrics;
    oos_equity: Series;
    trials_tested: number;
  } | null;
  cost_stress: { cost_multiplier: number; cagr_pct: number; sharpe: number; max_drawdown_pct: number; fees_paid: number }[];
  bootstrap: { horizon_days: number; cagr_pct: Record<string, number>; sharpe: Record<string, number>; max_drawdown_pct: Record<string, number>; prob_losing_year: number | null };
  lookahead: { has_bias: boolean; checked_indicators: string[]; decisions_checked: number; decisions_mismatched: number; biased_indicators: string[] };
  runtime_sec: number;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export default function StudyDetail() {
  const { key = "" } = useParams();
  const tk = useTokens(["--series-1", "--series-muted", "--div-neg"]);
  const [scale, setScale] = useState<"lin" | "log">("log");
  const q = useQuery({ queryKey: ["study", key], queryFn: () => api<Study>(`/api/research/${key}`), staleTime: 300_000 });
  const s = q.data;
  const m = s?.full.metrics;
  const eqSeries = useMemo(() => {
    if (!s) return [];
    const out = [{ id: "s", label: s.strategy, data: s.full.equity, color: tk["--series-1"] }];
    if (s.full.benchmark) out.push({ id: "b", label: "Buy & hold", data: s.full.benchmark, color: tk["--series-muted"] });
    return out;
  }, [s, tk]);
  const ddSeries = useMemo(() => (s ? [{ id: "dd", label: "Drawdown", data: s.full.drawdown, color: tk["--div-neg"], kind: "area" as const }] : []), [s, tk]);
  const wfSeries = useMemo(() => (s?.walk_forward ? [{ id: "wf", label: "Walk-forward OOS equity", data: s.walk_forward.oos_equity, color: tk["--series-1"], kind: "area" as const }] : []), [s, tk]);
  if (q.error) return <ErrorNote error={q.error} />;
  if (!s || !m) return <div className="empty">Loading…</div>;

  const years = Object.keys(m.yearly_returns ?? {});
  const benchYears: Record<string, number> = {};
  if (s.full.benchmark && s.full.benchmark.length) {
    const byYear = new Map<string, [number, number]>();
    for (const [t, v] of s.full.benchmark) {
      const y = new Date(t).getUTCFullYear().toString();
      const cur = byYear.get(y);
      byYear.set(y, cur ? [cur[0], v] : [v, v]);
    }
    let prevEnd: number | null = null;
    for (const [y, [a, b]] of byYear) {
      benchYears[y] = ((b / (prevEnd ?? a)) - 1) * 100;
      prevEnd = b;
    }
  }
  const monthlyYears = Object.keys(m.monthly_returns ?? {});
  const hm = s.grid.heatmap;
  const hl: [number, number] | undefined = hm
    ? [hm.y_values.findIndex((v) => v === s.full.hp[hm.y]), hm.x_values.findIndex((v) => v === s.full.hp[hm.x])]
    : undefined;
  const wf = s.walk_forward;
  const bm = m.benchmark ?? {};

  return (
    <>
      <PageHead
        title={<span className="row" style={{ gap: 10 }}>{s.strategy} <VerdictBadge verdict={s.verdict} /></span>}
        sub={<>{s.title}. {s.thesis}</>}
        actions={<Link className="btn" to="/research">All studies</Link>}
      />
      <div className="stack">
        <div className="grid grid-5">
          <StatTile label="CAGR" value={pct(m.cagr_pct)} delta={`buy & hold ${pct(bm.cagr_pct)}`} />
          <StatTile label="Sharpe" value={num(m.sharpe)} delta={`buy & hold ${num(bm.sharpe)}`} />
          <StatTile label="Max drawdown" value={pct(m.max_drawdown_pct)} delta={`buy & hold ${pct(bm.max_drawdown_pct)}`} />
          <StatTile label="Out-of-sample Sharpe" value={num(wf?.oos_metrics?.sharpe)} delta={wf ? `CAGR ${pct(wf.oos_metrics.cagr_pct)} · DD ${pct(wf.oos_metrics.max_drawdown_pct)}` : "no walk-forward"} />
          <StatTile label="Deflated Sharpe / PBO" value={`${num(s.dsr)} / ${num(s.pbo?.pbo)}`} delta={`${s.grid.summary.n ?? 1} variants tested`} />
        </div>

        <Card title="Validation gates" sub={`Verdict: ${s.verdict}`}>
          <ul className="gates">
            {s.verdict_reasons.map((r) => {
              const pass = r.startsWith("PASS");
              return (
                <li key={r}>
                  <span style={{ color: pass ? "var(--good)" : "var(--critical)", marginTop: 2 }}>{pass ? Icon.check(14) : Icon.x(14)}</span>
                  <span><b>{pass ? "Pass" : "Fail"}</b> <span className="secondary">{r.slice(5)}</span></span>
                </li>
              );
            })}
          </ul>
        </Card>

        <ChartFrame
          title="Equity vs buy & hold"
          sub={`$10,000 start · ${date(Date.parse(m.start))} → ${date(Date.parse(m.end))} · fees ${pct(Number(s.config.fee_taker) * 100, 2)} + ${s.config.slippage_bps} bps slippage per side`}
          legend={<div className="legend"><LegendItem color={tk["--series-1"]} label={s.strategy} /><LegendItem color={tk["--series-muted"]} label="Buy & hold" /></div>}
          toolbar={<Segmented value={scale} onChange={setScale} options={[{ value: "lin", label: "Linear" }, { value: "log", label: "Log" }]} label="Scale" />}
          table={{ columns: ["Date", s.strategy, "Buy & hold"], rows: s.full.equity.filter((_, i) => i % 5 === 0).map(([t, v]) => { const b = s.full.benchmark?.find((p) => p[0] >= t); return [date(t), money(v), b ? money(b[1]) : "—"]; }) }}
        >
          <TimeSeriesChart series={eqSeries} height={320} log={scale === "log"} format={(v) => money(v)} />
        </ChartFrame>

        <div className="grid grid-2">
          <ChartFrame title="Drawdown" sub="Decline from the running equity peak" table={{ columns: ["Date", "Drawdown"], rows: s.full.drawdown.filter((_, i) => i % 5 === 0).map(([t, v]) => [date(t), pct(v)]) }}>
            <TimeSeriesChart series={ddSeries} height={220} format={(v) => pct(v)} axisFormat={(v) => `${v.toFixed(0)}%`} />
          </ChartFrame>
          <ChartFrame
            title="Yearly returns"
            sub="Strategy vs buy & hold (bars capped at ±300% for readability; exact values in tooltip/table)"
            legend={<div className="legend"><LegendItem color={tk["--series-1"]} label={s.strategy} kind="rect" /><LegendItem color={tk["--series-muted"]} label="Buy & hold" kind="rect" /></div>}
            table={{ columns: ["Year", s.strategy, "Buy & hold"], rows: years.map((y) => [y, pct(m.yearly_returns[y]), pct(benchYears[y])]) }}
          >
            <BarChart
              categories={years}
              clampPct={300}
              series={[
                { label: s.strategy, color: tk["--series-1"], values: years.map((y) => m.yearly_returns[y]) },
                { label: "Buy & hold", color: tk["--series-muted"], values: years.map((y) => benchYears[y] ?? null) },
              ]}
            />
          </ChartFrame>
        </div>

        <Card title="Monthly returns" sub="Blue = gain, red = loss; intensity scales with size (capped at ±30%)">
          <Heatmap
            rows={monthlyYears}
            cols={MONTHS}
            values={monthlyYears.map((y) => MONTHS.map((_, i) => m.monthly_returns[y]?.[String(i + 1)] ?? null))}
            mode="diverging"
            domain={[-30, 30]}
            format={(v) => v.toFixed(1)}
            corner="%"
          />
        </Card>

        {wf && (
          <div className="grid grid-2">
            <ChartFrame title="Walk-forward: out-of-sample equity" sub={`Stitched test windows · ${wf.trials_tested} optimisation runs`} table={{ columns: ["Date", "Equity"], rows: wf.oos_equity.filter((_, i) => i % 5 === 0).map(([t, v]) => [date(t), money(v)]) }}>
              <TimeSeriesChart series={wfSeries} height={240} log format={(v) => money(v)} />
            </ChartFrame>
            <Card title="Walk-forward folds" sub="Parameters chosen on the training window (robust plateau), then traded blind" flush>
              <DataTable
                rows={wf.folds}
                rowKey={(f) => f.test_window[0]}
                maxHeight={300}
                columns={[
                  { key: "test", label: "Test window", render: (f) => `${f.test_window[0].slice(0, 7)} → ${f.test_window[1].slice(0, 7)}` },
                  { key: "hp", label: "Chosen parameters", render: (f) => <span className="mono wrap" style={{ fontSize: 11.5 }}>{Object.entries(f.hp).map(([k, v]) => `${k}=${v}`).join(" ")}</span> },
                  { key: "sharpe", label: "OOS Sharpe", align: "right", render: (f) => num(f.test_metrics.sharpe) },
                  { key: "ret", label: "OOS return", align: "right", render: (f) => <span className={upDown(f.test_metrics.total_return_pct)}>{pct(f.test_metrics.total_return_pct)}</span> },
                ]}
              />
            </Card>
          </div>
        )}

        <div className="grid grid-2">
          {hm && (
            <Card title="Parameter robustness" sub={`Sharpe across ${hm.x} × ${hm.y} (other parameters fixed: ${Object.entries(hm.fixed).map(([k, v]) => `${k}=${v}`).join(", ") || "none"}). Outlined cell = default.`}>
              <Heatmap
                rows={hm.y_values.map(String)}
                cols={hm.x_values.map(String)}
                values={hm.sharpe}
                mode="sequential"
                format={(v) => v.toFixed(2)}
                highlight={hl && hl[0] >= 0 && hl[1] >= 0 ? hl : undefined}
                corner={`${hm.y} ↓ / ${hm.x} →`}
              />
              <p className="secondary mt" style={{ fontSize: 12.5 }}>
                Grid: median Sharpe {num(s.grid.summary.median)}, {pct((s.grid.summary.share_above_0_5 ?? 0) * 100, 0)} of variants above 0.5. PBO {num(s.pbo?.pbo)} over{" "}
                {s.pbo?.n_combinations ?? 0} CSCV splits.
              </p>
            </Card>
          )}
          <Card title="Robustness checks" flush>
            <DataTable
              rows={s.cost_stress}
              rowKey={(c) => String(c.cost_multiplier)}
              columns={[
                { key: "cost_multiplier", label: "Costs", render: (c) => (c.cost_multiplier === 0 ? "none" : `${c.cost_multiplier}×`) },
                { key: "cagr_pct", label: "CAGR", align: "right", render: (c) => pct(c.cagr_pct) },
                { key: "sharpe", label: "Sharpe", align: "right", render: (c) => num(c.sharpe) },
                { key: "max_drawdown_pct", label: "Max DD", align: "right", render: (c) => pct(c.max_drawdown_pct) },
              ]}
            />
            <div className="card-body">
              <h3 style={{ marginBottom: 6 }}>Block bootstrap ({Math.round(s.bootstrap.horizon_days / 365)}-year paths)</h3>
              <dl className="kv">
                <dt>CAGR 5th / 50th / 95th pct</dt><dd>{pct(s.bootstrap.cagr_pct.p5)} / {pct(s.bootstrap.cagr_pct.p50)} / {pct(s.bootstrap.cagr_pct.p95)}</dd>
                <dt>Max drawdown 5th / 50th pct</dt><dd>{pct(s.bootstrap.max_drawdown_pct.p5)} / {pct(s.bootstrap.max_drawdown_pct.p50)}</dd>
                <dt>Sharpe 5th / 50th pct</dt><dd>{num(s.bootstrap.sharpe.p5)} / {num(s.bootstrap.sharpe.p50)}</dd>
                <dt>P(losing first year)</dt><dd>{pct((s.bootstrap.prob_losing_year ?? 0) * 100, 0)}</dd>
                <dt>Look-ahead check</dt>
                <dd>{s.lookahead.has_bias ? <span className="down">bias found: {s.lookahead.biased_indicators.join(", ")}</span> : `clean (${s.lookahead.checked_indicators.length} indicators, ${s.lookahead.decisions_checked} decisions replayed)`}</dd>
              </dl>
            </div>
          </Card>
        </div>

        <Card title="Sub-periods" flush>
          <DataTable
            rows={s.subperiods.map((p) => ({ ...p, bench: s.benchmark_subperiods?.find((b) => b.period === p.period) }))}
            rowKey={(p) => p.period}
            columns={[
              { key: "period", label: "Period" },
              { key: "cagr_pct", label: "CAGR", align: "right", render: (p) => pct(p.cagr_pct) },
              { key: "sharpe", label: "Sharpe", align: "right", render: (p) => num(p.sharpe) },
              { key: "max_drawdown_pct", label: "Max DD", align: "right", render: (p) => pct(p.max_drawdown_pct) },
              { key: "b_sharpe", label: "B&H Sharpe", align: "right", render: (p) => <span className="muted">{num(p.bench?.sharpe)}</span> },
              { key: "b_dd", label: "B&H Max DD", align: "right", render: (p) => <span className="muted">{pct(p.bench?.max_drawdown_pct)}</span> },
            ]}
          />
        </Card>

        <Card title="Trade statistics" sub={`${m.total_trades} trades · win rate ${pct(m.win_rate)} · profit factor ${num(m.profit_factor)} · avg hold ${hours(m.avg_holding_hours)} · exposure ${pct(m.exposure_pct, 0)}`} flush>
          <DataTable<Trade>
            rows={s.full.trades}
            rowKey={(t, i) => `${t.id}-${i}`}
            maxHeight={360}
            initialSort={{ key: "closed_at", dir: "desc" }}
            columns={[
              { key: "opened_at", label: "Opened", render: (t) => dateTime(t.opened_at) },
              { key: "closed_at", label: "Closed", render: (t) => dateTime(t.closed_at) },
              { key: "side", label: "Side" },
              { key: "entry_price", label: "Entry", align: "right", render: (t) => price(t.entry_price) },
              { key: "exit_price", label: "Exit", align: "right", render: (t) => price(t.exit_price) },
              { key: "return_pct", label: "Return", align: "right", render: (t) => <span className={upDown(t.return_pct)}>{pct(t.return_pct, 2, true)}</span> },
              { key: "pnl", label: "P&L", align: "right", render: (t) => <span className={upDown(t.pnl)}>{signedMoney(t.pnl)}</span> },
              { key: "exit_reason", label: "Exit reason", render: (t) => <span className="muted">{t.exit_reason}</span> },
            ]}
          />
        </Card>
        <p className="muted" style={{ fontSize: 12 }}>
          Parameters: <span className="mono">{JSON.stringify(s.full.hp)}</span> · computed in {s.runtime_sec}s. Research results are historical simulations, not a promise of future returns.
        </p>
      </div>
    </>
  );
}
