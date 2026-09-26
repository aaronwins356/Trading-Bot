import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, type BacktestRow, post, type StrategyInfo } from "../api";
import { CandleChart, Heatmap, type Marker, TimeSeriesChart } from "../components/charts";
import { ChartFrame, LegendItem } from "../components/ChartFrame";
import { DataTable } from "../components/DataTable";
import { Card, ErrorNote, PageHead, Segmented, StatTile, VerdictBadge } from "../components/ui";
import { ago, date, dateTime, money, num, pct, price, signedMoney, upDown } from "../format";
import { useTokens } from "../theme";

interface Dataset {
  exchange: string;
  symbol: string;
  timeframe: string;
  candles: number;
  start: string;
  end: string;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function Results({ row }: { row: BacktestRow }) {
  const tk = useTokens(["--series-1", "--series-muted", "--div-neg"]);
  const [scale, setScale] = useState<"lin" | "log">("log");
  const r = row.result!;
  const m = r.metrics;
  const eq = useMemo(() => {
    const out = [{ id: "s", label: r.strategy, data: r.equity, color: tk["--series-1"] }];
    if (r.benchmark) out.push({ id: "b", label: "Buy & hold", data: r.benchmark, color: tk["--series-muted"] });
    return out;
  }, [r, tk]);
  const dd = useMemo(() => [{ id: "dd", label: "Drawdown", data: r.drawdown, color: tk["--div-neg"], kind: "area" as const }], [r, tk]);
  const years = Object.keys(m.monthly_returns ?? {});
  const bm = m.benchmark ?? {};
  return (
    <div className="stack">
      <div className="grid grid-5">
        <StatTile label="Total return" value={pct(m.total_return_pct)} delta={`CAGR ${pct(m.cagr_pct)}`} deltaClass={upDown(m.total_return_pct)} />
        <StatTile label="Sharpe" value={num(m.sharpe)} delta={`Sortino ${num(m.sortino)} · B&H ${num(bm.sharpe)}`} />
        <StatTile label="Max drawdown" value={pct(m.max_drawdown_pct)} delta={`B&H ${pct(bm.max_drawdown_pct)}`} />
        <StatTile label="Trades" value={String(m.total_trades)} delta={`win ${pct(m.win_rate)} · PF ${num(m.profit_factor)}`} />
        <StatTile label="Fees paid" value={money(m.fees_paid)} delta={`exposure ${pct(m.exposure_pct, 0)} · ${r.runtime_sec}s`} />
      </div>
      <ChartFrame
        title="Equity vs buy & hold"
        legend={<div className="legend"><LegendItem color={tk["--series-1"]} label={r.strategy} />{r.benchmark && <LegendItem color={tk["--series-muted"]} label="Buy & hold" />}</div>}
        toolbar={<Segmented value={scale} onChange={setScale} options={[{ value: "lin", label: "Linear" }, { value: "log", label: "Log" }]} label="Scale" />}
        table={{ columns: ["Time", "Equity"], rows: r.equity.filter((_, i) => i % 5 === 0).map(([t, v]) => [dateTime(t), money(v)]) }}
      >
        <TimeSeriesChart series={eq} height={300} log={scale === "log"} format={(v) => money(v)} />
      </ChartFrame>
      <ChartFrame title="Drawdown" table={{ columns: ["Time", "Drawdown"], rows: r.drawdown.filter((_, i) => i % 5 === 0).map(([t, v]) => [dateTime(t), pct(v)]) }}>
        <TimeSeriesChart series={dd} height={180} format={(v) => pct(v)} axisFormat={(v) => `${v.toFixed(0)}%`} />
      </ChartFrame>
      {r.candles && (
        <ChartFrame title="Trades on price" sub={`${r.symbols[0]} · ${r.candles_timeframe} candles (last 400 trades marked)`} table={{ columns: ["Time", "Close"], rows: r.candles.slice(-200).reverse().map((c) => [dateTime(c[0]), price(c[4])]) }}>
          <CandleChart candles={r.candles} markers={(r.markers ?? []) as Marker[]} height={360} />
        </ChartFrame>
      )}
      {years.length > 0 && (
        <Card title="Monthly returns (%)">
          <Heatmap rows={years} cols={MONTHS} values={years.map((y) => MONTHS.map((_, i) => m.monthly_returns[y]?.[String(i + 1)] ?? null))} mode="diverging" domain={[-30, 30]} format={(v) => v.toFixed(1)} corner="%" />
        </Card>
      )}
      <Card title="Trades" flush>
        <DataTable
          rows={r.trades}
          rowKey={(t, i) => `${t.id}-${i}`}
          maxHeight={380}
          initialSort={{ key: "closed_at", dir: "desc" }}
          columns={[
            { key: "opened_at", label: "Opened", render: (t) => dateTime(t.opened_at) },
            { key: "closed_at", label: "Closed", render: (t) => dateTime(t.closed_at) },
            { key: "symbol", label: "Symbol" },
            { key: "side", label: "Side" },
            { key: "entry_price", label: "Entry", align: "right", render: (t) => price(t.entry_price) },
            { key: "exit_price", label: "Exit", align: "right", render: (t) => price(t.exit_price) },
            { key: "return_pct", label: "Return", align: "right", render: (t) => <span className={upDown(t.return_pct)}>{pct(t.return_pct, 2, true)}</span> },
            { key: "pnl", label: "P&L", align: "right", render: (t) => <span className={upDown(t.pnl)}>{signedMoney(t.pnl)}</span> },
            { key: "exit_reason", label: "Exit", render: (t) => <span className="muted">{t.exit_reason}</span> },
          ]}
        />
      </Card>
    </div>
  );
}

export default function BacktestPage() {
  const qc = useQueryClient();
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => api<StrategyInfo[]>("/api/strategies"), staleTime: 300_000 });
  const data = useQuery({ queryKey: ["datasets"], queryFn: () => api<Dataset[]>("/api/data"), staleTime: 60_000 });
  const history = useQuery({ queryKey: ["backtests"], queryFn: () => api<BacktestRow[]>("/api/backtests") });
  const [strategy, setStrategy] = useState("TrendVolTarget");
  const [dataset, setDataset] = useState("bitstamp|BTC/USD");
  const [timeframe, setTimeframe] = useState("1d");
  const [start, setStart] = useState("2018-01-01");
  const [end, setEnd] = useState("");
  const [fee, setFee] = useState(0.1);
  const [slip, setSlip] = useState(5);
  const [market, setMarket] = useState<"spot" | "futures">("spot");
  const [leverage, setLeverage] = useState(2);
  const [hp, setHp] = useState<Record<string, unknown>>({});
  const [active, setActive] = useState<string | null>(null);

  const info = strategies.data?.find((s) => s.name === strategy);
  const portfolio = info?.kind === "portfolio";
  const routeSets = useMemo(() => {
    const seen = new Map<string, Dataset[]>();
    for (const d of data.data ?? []) {
      if (d.exchange === "coinmetrics") continue;
      const k = `${d.exchange}|${d.symbol}`;
      seen.set(k, [...(seen.get(k) ?? []), d]);
    }
    return seen;
  }, [data.data]);
  const tfs = routeSets.get(dataset)?.map((d) => d.timeframe) ?? ["1d"];

  useEffect(() => {
    if (info) {
      setTimeframe(info.kind === "portfolio" ? "1d" : info.timeframe);
      setHp({});
    }
  }, [info]);

  const run = useMutation({
    mutationFn: () => {
      const [exchange, symbol] = dataset.split("|");
      return post<{ id: string }>("/api/backtests", {
        strategy,
        exchange,
        symbols: [symbol],
        timeframe,
        start: start || null,
        end: end || null,
        hp,
        fee_maker: fee / 100,
        fee_taker: fee / 100,
        slippage_bps: slip,
        exchange_type: market,
        leverage: market === "futures" ? leverage : 1,
        funding_rate_8h: market === "futures" ? 0.0001 : 0,
      });
    },
    onSuccess: (r) => {
      setActive(r.id);
      qc.invalidateQueries({ queryKey: ["backtests"] });
    },
  });
  const job = useQuery({
    queryKey: ["backtest", active],
    queryFn: () => api<BacktestRow>(`/api/backtests/${active}`),
    enabled: !!active,
    refetchInterval: (q) => (q.state.data && ["done", "error"].includes(q.state.data.status) ? false : 700),
  });
  useEffect(() => {
    if (job.data?.status === "done") qc.invalidateQueries({ queryKey: ["backtests"] });
  }, [job.data?.status, qc]);

  return (
    <>
      <PageHead title="Backtest lab" sub="Event-driven, look-ahead-free simulation with fees, slippage, stops filled on the intrabar price path, and funding for perpetuals." />
      <div className="stack">
        <Card title="Configure">
          <div className="stack">
            <div className="form-grid">
              <label className="field">Strategy
                <select className="input" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
                  {(strategies.data ?? []).map((s) => <option key={s.name} value={s.name}>{s.name}</option>)}
                </select>
              </label>
              {portfolio ? (
                <label className="field">Universe<input className="input" value="Top-20 coins by market cap (point-in-time)" readOnly /></label>
              ) : (
                <label className="field">Dataset
                  <select className="input" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                    {[...routeSets.keys()].map((k) => <option key={k} value={k}>{k.replace("|", " · ")}</option>)}
                  </select>
                </label>
              )}
              <label className="field">Timeframe
                <select className="input" value={timeframe} onChange={(e) => setTimeframe(e.target.value)}>
                  {(portfolio ? ["1d"] : tfs).map((t) => <option key={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">Start<input className="input" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
              <label className="field">End (blank = latest)<input className="input" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
              <label className="field">Fee % per side<input className="input num" type="number" step="0.01" value={fee} onChange={(e) => setFee(Number(e.target.value))} /></label>
              <label className="field">Slippage (bps)<input className="input num" type="number" step="1" value={slip} onChange={(e) => setSlip(Number(e.target.value))} /></label>
              <label className="field">Market
                <select className="input" value={market} onChange={(e) => setMarket(e.target.value as "spot" | "futures")}>
                  <option value="spot">spot</option>
                  <option value="futures">perpetual futures</option>
                </select>
              </label>
              {market === "futures" && <label className="field">Account leverage<input className="input num" type="number" step="0.5" value={leverage} onChange={(e) => setLeverage(Number(e.target.value))} /></label>}
            </div>
            {info && (
              <div className="notice"><div className="row" style={{ marginBottom: 4 }}><VerdictBadge verdict={info.status} /> <b>{info.name}</b></div>{info.description}</div>
            )}
            {info && info.hyperparameters.length > 0 && (
              <div className="form-grid">
                {info.hyperparameters.map((p) => (
                  <label className="field" key={p.name} title={p.min !== undefined ? `range ${p.min} – ${p.max}` : undefined}>
                    {p.name}
                    {p.type === "categorical" ? (
                      <select className="input" value={String(hp[p.name] ?? p.default)} onChange={(e) => setHp({ ...hp, [p.name]: e.target.value === "true" ? true : e.target.value === "false" ? false : e.target.value })}>
                        {(p.options ?? []).map((o) => <option key={String(o)}>{String(o)}</option>)}
                      </select>
                    ) : (
                      <input className="input num" type="number" step={p.type === "int" ? 1 : "any"} value={String(hp[p.name] ?? p.default)} onChange={(e) => setHp({ ...hp, [p.name]: p.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value) })} />
                    )}
                  </label>
                ))}
              </div>
            )}
            <ErrorNote error={run.error} />
            <div className="row">
              <button type="button" className="btn primary" disabled={run.isPending || (job.data && !["done", "error"].includes(job.data.status))} onClick={() => run.mutate()}>
                Run backtest
              </button>
              {job.data && !["done", "error"].includes(job.data.status) && (
                <div style={{ width: 240 }}>
                  <div className="bar-track"><div className="bar-fill" style={{ width: `${(job.data.progress || 0.02) * 100}%` }} /></div>
                </div>
              )}
            </div>
          </div>
        </Card>
        {job.data?.status === "error" && <div className="notice danger"><div className="pre">{job.data.error}</div></div>}
        {job.data?.status === "done" && job.data.result && <Results row={job.data} />}
        <Card title="History" flush>
          <DataTable<BacktestRow>
            rows={history.data ?? []}
            rowKey={(b) => b.id}
            onRowClick={(b) => setActive(b.id)}
            empty="No backtests yet."
            columns={[
              { key: "created_at", label: "When", render: (b) => ago(b.created_at) },
              { key: "strategy", label: "Strategy", render: (b) => String(b.request.strategy) },
              { key: "data", label: "Data", render: (b) => `${(b.request.symbols as string[])?.[0] ?? ""} ${b.request.timeframe} from ${date(Date.parse(String(b.request.start)))}` },
              { key: "status", label: "Status" },
              { key: "cagr", label: "CAGR", align: "right", render: (b) => pct(b.summary?.cagr_pct) },
              { key: "sharpe", label: "Sharpe", align: "right", render: (b) => num(b.summary?.sharpe) },
              { key: "dd", label: "Max DD", align: "right", render: (b) => pct(b.summary?.max_drawdown_pct) },
              { key: "trades", label: "Trades", align: "right", render: (b) => String(b.summary?.total_trades ?? "—") },
            ]}
          />
        </Card>
      </div>
    </>
  );
}
