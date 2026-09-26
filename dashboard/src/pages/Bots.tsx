import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type BotConfig, type BotStatus, post, type StrategyInfo } from "../api";
import { DataTable } from "../components/DataTable";
import { BotStatusBadge, Card, ErrorNote, Icon, ModeBadge, PageHead, RiskBadge, Segmented, VerdictBadge } from "../components/ui";
import { money, pct, upDown } from "../format";

type Mode = "replay" | "paper" | "live";

interface Draft {
  name: string;
  strategy: string;
  symbols: string;
  timeframe: string;
  mode: Mode;
  capital: number;
  exchangeId: string;
  marketType: "spot" | "futures";
  sandbox: boolean;
  apiKeyEnv: string;
  secretEnv: string;
  replayExchange: string;
  replayStart: string;
  replaySpeed: number;
  feeMaker: number;
  feeTaker: number;
  slippage: number;
  leverage: number;
  maxDD: number;
  dailyLoss: number;
  maxPos: number;
  maxGross: number;
  hp: Record<string, unknown>;
  jevEnabled: boolean;
  jevUrl: string;
  jevModel: string;
  jevAuthority: "advisory" | "veto" | "full";
  jevEffort: "low" | "medium" | "high";
  autostart: boolean;
}

const BASE: Draft = {
  name: "BTC trend (replay demo)",
  strategy: "TrendVolTarget",
  symbols: "BTC/USD",
  timeframe: "1d",
  mode: "replay",
  capital: 10000,
  exchangeId: "binance",
  marketType: "spot",
  sandbox: true,
  apiKeyEnv: "BINANCE_API_KEY",
  secretEnv: "BINANCE_API_SECRET",
  replayExchange: "bitstamp",
  replayStart: "2023-01-01",
  replaySpeed: 0.05,
  feeMaker: 0.1,
  feeTaker: 0.1,
  slippage: 5,
  leverage: 1,
  maxDD: 30,
  dailyLoss: 6,
  maxPos: 100,
  maxGross: 100,
  hp: {},
  jevEnabled: false,
  jevUrl: "http://localhost:11434/v1",
  jevModel: "gpt-oss:20b",
  jevAuthority: "veto",
  jevEffort: "medium",
  autostart: false,
};

const PRESETS: { label: string; d: Partial<Draft> }[] = [
  { label: "Replay demo · BTC trend", d: { name: "BTC trend (replay demo)", strategy: "TrendVolTarget", mode: "replay", timeframe: "1d", symbols: "BTC/USD", replayStart: "2023-01-01" } },
  { label: "Paper · BTC trend (Binance)", d: { name: "BTC trend (paper)", strategy: "TrendVolTarget", mode: "paper", timeframe: "1d", symbols: "BTC/USDT", exchangeId: "binance" } },
  { label: "Paper · Turtle 4h", d: { name: "BTC Turtle 4h (paper)", strategy: "DonchianBreakout", mode: "paper", timeframe: "4h", symbols: "BTC/USDT", exchangeId: "binance" } },
  { label: "JEV AI · replay", d: { name: "JEV decision AI (replay)", strategy: "JEVStrategy", mode: "replay", timeframe: "1d", symbols: "BTC/USD", jevEnabled: true } },
];

function toConfig(d: Draft, strategies: StrategyInfo[]): BotConfig {
  const id = d.name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "").slice(0, 40) + "-" + Math.random().toString(16).slice(2, 6);
  const info = strategies.find((s) => s.name === d.strategy);
  const hp: Record<string, unknown> = {};
  for (const p of info?.hyperparameters ?? []) if (d.hp[p.name] !== undefined && d.hp[p.name] !== p.default) hp[p.name] = d.hp[p.name];
  return {
    id,
    name: d.name,
    strategy: d.strategy,
    symbols: d.symbols.split(",").map((s) => s.trim()).filter(Boolean),
    timeframe: d.timeframe,
    mode: d.mode,
    capital: d.capital,
    hp,
    exchange: { id: d.exchangeId, market_type: d.marketType, api_key_env: d.mode === "live" ? d.apiKeyEnv : null, secret_env: d.mode === "live" ? d.secretEnv : null, password_env: null, sandbox: d.sandbox },
    costs: { fee_maker: d.feeMaker / 100, fee_taker: d.feeTaker / 100, slippage_bps: d.slippage, leverage: d.marketType === "futures" ? d.leverage : 1, funding_rate_8h: d.marketType === "futures" ? 0.0001 : 0 },
    risk: {
      max_position_pct: d.maxPos / 100,
      max_gross_exposure: d.maxGross / 100,
      max_open_positions: 10,
      min_order_notional: 10,
      max_price_deviation_pct: 10,
      daily_loss_limit_pct: d.dailyLoss,
      max_drawdown_pct: d.maxDD,
      max_orders_per_minute: 30,
      flatten_on_halt: false,
      protections: [],
    },
    jev: {
      enabled: d.jevEnabled,
      base_url: d.jevUrl,
      model: d.jevModel,
      api_key_env: null,
      authority: d.jevAuthority,
      temperature: 0.2,
      max_tokens: 800,
      timeout_s: 90,
      reasoning_effort: d.jevEffort,
      min_confidence: 0.5,
      max_exposure: 1,
      decide_every_bars: 1,
      anonymize: true,
      analysts: ["tsmom", "donchian", "ma_trend", "breakout_20d"],
    },
    replay: { exchange: d.replayExchange, start: d.replayStart || null, speed: d.replaySpeed, warmup_bars: 400 },
    warmup_bars: d.timeframe === "1h" ? 1000 : 400,
    candle_delay_s: 5,
    autostart: d.autostart,
  };
}

function Field({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <label className="field" title={hint}>
      {label}
      {children}
    </label>
  );
}

function NewBotForm({ strategies, onCreated }: { strategies: StrategyInfo[]; onCreated: (id: string) => void }) {
  const [d, setD] = useState<Draft>(BASE);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((x) => ({ ...x, [k]: v }));
  const info = useMemo(() => strategies.find((s) => s.name === d.strategy), [strategies, d.strategy]);
  const create = useMutation({
    mutationFn: (cfg: BotConfig) => post<BotStatus>("/api/bots", cfg),
    onSuccess: (b) => onCreated(b.id),
  });
  const numIn = (k: keyof Draft, step = "any") => (
    <input className="input num" type="number" step={step} value={d[k] as number} onChange={(e) => set(k, Number(e.target.value) as never)} />
  );
  return (
    <Card title="Create a bot" sub="Replay needs no exchange; Paper uses live prices with simulated fills; Live sends real orders (keys stay in server env vars).">
      <div className="stack">
        <div className="row">
          {PRESETS.map((p) => (
            <button key={p.label} type="button" className="btn sm" onClick={() => setD({ ...BASE, ...p.d, hp: {} })}>
              {p.label}
            </button>
          ))}
        </div>
        <div className="form-grid">
          <Field label="Name">
            <input className="input" value={d.name} onChange={(e) => set("name", e.target.value)} />
          </Field>
          <Field label="Strategy">
            <select className="input" value={d.strategy} onChange={(e) => setD((x) => ({ ...x, strategy: e.target.value, hp: {}, timeframe: strategies.find((s) => s.name === e.target.value)?.timeframe ?? x.timeframe }))}>
              {strategies.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.name} — {s.status}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Symbols (comma separated)">
            <input className="input" value={d.symbols} onChange={(e) => set("symbols", e.target.value)} />
          </Field>
          <Field label="Timeframe">
            <select className="input" value={d.timeframe} onChange={(e) => set("timeframe", e.target.value)}>
              {["15m", "1h", "4h", "1d"].map((t) => <option key={t}>{t}</option>)}
            </select>
          </Field>
          <Field label="Capital (quote currency)">{numIn("capital", "100")}</Field>
          <Field label="Mode">
            <Segmented<Mode> value={d.mode} onChange={(v) => set("mode", v)} options={[{ value: "replay", label: "Replay" }, { value: "paper", label: "Paper" }, { value: "live", label: "Live" }]} />
          </Field>
        </div>
        {info && (
          <div className="notice">
            <div className="row" style={{ marginBottom: 4 }}>
              <VerdictBadge verdict={info.status} />
              <b>{info.name}</b> <span className="muted">{info.kind} · suggested {info.timeframe}</span>
            </div>
            {info.summary}
          </div>
        )}
        {info && info.hyperparameters.length > 0 && (
          <>
            <h3>Hyperparameters</h3>
            <div className="form-grid">
              {info.hyperparameters.map((p) => (
                <Field key={p.name} label={p.name} hint={p.min !== undefined ? `range ${p.min} – ${p.max}` : undefined}>
                  {p.type === "categorical" ? (
                    <select className="input" value={String(d.hp[p.name] ?? p.default)} onChange={(e) => set("hp", { ...d.hp, [p.name]: e.target.value === "true" ? true : e.target.value === "false" ? false : e.target.value })}>
                      {(p.options ?? []).map((o) => <option key={String(o)} value={String(o)}>{String(o)}</option>)}
                    </select>
                  ) : (
                    <input
                      className="input num"
                      type="number"
                      step={p.type === "int" ? 1 : "any"}
                      min={p.min}
                      max={p.max}
                      value={String(d.hp[p.name] ?? p.default)}
                      onChange={(e) => set("hp", { ...d.hp, [p.name]: p.type === "int" ? parseInt(e.target.value, 10) : parseFloat(e.target.value) })}
                    />
                  )}
                </Field>
              ))}
            </div>
          </>
        )}
        {d.mode === "replay" ? (
          <div className="form-grid">
            <Field label="Replay dataset (exchange)"><input className="input" value={d.replayExchange} onChange={(e) => set("replayExchange", e.target.value)} /></Field>
            <Field label="Replay from"><input className="input" type="date" value={d.replayStart} onChange={(e) => set("replayStart", e.target.value)} /></Field>
            <Field label="Seconds per candle" hint="0 = as fast as possible">{numIn("replaySpeed")}</Field>
          </div>
        ) : (
          <div className="form-grid">
            <Field label="Exchange (CCXT id)"><input className="input" value={d.exchangeId} onChange={(e) => set("exchangeId", e.target.value)} /></Field>
            <Field label="Market">
              <select className="input" value={d.marketType} onChange={(e) => set("marketType", e.target.value as "spot" | "futures")}>
                <option value="spot">spot</option>
                <option value="futures">futures (perpetual)</option>
              </select>
            </Field>
            {d.marketType === "futures" && <Field label="Leverage cap">{numIn("leverage", "0.5")}</Field>}
            {d.mode === "live" && (
              <>
                <Field label="API key env var"><input className="input mono" value={d.apiKeyEnv} onChange={(e) => set("apiKeyEnv", e.target.value)} /></Field>
                <Field label="Secret env var"><input className="input mono" value={d.secretEnv} onChange={(e) => set("secretEnv", e.target.value)} /></Field>
                <label className="check"><input type="checkbox" checked={d.sandbox} onChange={(e) => set("sandbox", e.target.checked)} /> Use exchange testnet (sandbox)</label>
              </>
            )}
          </div>
        )}
        {d.mode === "live" && !d.sandbox && (
          <div className="notice danger">Live mode with sandbox off trades real money. Run the same bot in paper mode for weeks first and start with a small capital allocation.</div>
        )}
        <details>
          <summary className="secondary" style={{ cursor: "pointer" }}>Costs & risk limits</summary>
          <div className="form-grid mt">
            <Field label="Maker fee %">{numIn("feeMaker")}</Field>
            <Field label="Taker fee %">{numIn("feeTaker")}</Field>
            <Field label="Slippage (bps)">{numIn("slippage")}</Field>
            <Field label="Kill switch: max drawdown %">{numIn("maxDD")}</Field>
            <Field label="Pause: daily loss %">{numIn("dailyLoss")}</Field>
            <Field label="Max position % of equity">{numIn("maxPos")}</Field>
            <Field label="Max gross exposure %">{numIn("maxGross")}</Field>
            <label className="check"><input type="checkbox" checked={d.autostart} onChange={(e) => set("autostart", e.target.checked)} /> Start automatically with the server</label>
          </div>
        </details>
        {d.strategy === "JEVStrategy" && (
          <div className="card" style={{ padding: 14 }}>
            <h3>JEV decision AI</h3>
            <p className="secondary" style={{ margin: "4px 0 10px" }}>Any OpenAI-compatible server with an open-weight model (Ollama, vLLM, LM Studio, llama.cpp). With the LLM off, JEV trades the quant consensus.</p>
            <div className="form-grid">
              <label className="check"><input type="checkbox" checked={d.jevEnabled} onChange={(e) => set("jevEnabled", e.target.checked)} /> Use the LLM</label>
              <Field label="Endpoint (base URL)"><input className="input mono" value={d.jevUrl} onChange={(e) => set("jevUrl", e.target.value)} /></Field>
              <Field label="Model"><input className="input mono" value={d.jevModel} onChange={(e) => set("jevModel", e.target.value)} /></Field>
              <Field label="Authority" hint="advisory: log only · veto: may only reduce · full: decides within limits">
                <select className="input" value={d.jevAuthority} onChange={(e) => set("jevAuthority", e.target.value as Draft["jevAuthority"])}>
                  <option value="veto">veto (safe default)</option>
                  <option value="advisory">advisory</option>
                  <option value="full">full</option>
                </select>
              </Field>
              <Field label="Reasoning effort">
                <select className="input" value={d.jevEffort} onChange={(e) => set("jevEffort", e.target.value as Draft["jevEffort"])}>
                  <option>low</option>
                  <option>medium</option>
                  <option>high</option>
                </select>
              </Field>
            </div>
          </div>
        )}
        <ErrorNote error={create.error} />
        <div className="row">
          <button type="button" className="btn primary" disabled={create.isPending || !d.name || !d.symbols} onClick={() => create.mutate(toConfig(d, strategies))}>
            {Icon.plus(14)} Create bot
          </button>
        </div>
      </div>
    </Card>
  );
}

export default function BotsPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api<BotStatus[]>("/api/bots"), refetchInterval: 10_000 });
  const strategies = useQuery({ queryKey: ["strategies"], queryFn: () => api<StrategyInfo[]>("/api/strategies"), staleTime: 300_000 });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "start" | "stop" }) => post(`/api/bots/${id}/${action}`),
    onSettled: () => qc.invalidateQueries({ queryKey: ["bots"] }),
  });
  return (
    <>
      <PageHead title="Bots" sub="Each bot runs one strategy on one or more symbols, with its own capital, risk limits and kill switch." />
      <div className="stack">
        <ErrorNote error={bots.error || act.error} />
        <Card title="Your bots" flush>
          <DataTable<BotStatus>
            rows={bots.data ?? []}
            rowKey={(b) => b.id}
            onRowClick={(b) => nav(`/bots/${b.id}`)}
            empty="No bots yet — create one below."
            columns={[
              { key: "name", label: "Bot", render: (b) => <b>{b.name}</b> },
              { key: "strategy", label: "Strategy" },
              { key: "symbols", label: "Symbols", render: (b) => `${b.symbols?.join(", ")} · ${b.timeframe}` },
              { key: "mode", label: "Mode", render: (b) => <ModeBadge mode={b.mode} /> },
              { key: "status", label: "Status", render: (b) => <BotStatusBadge status={b.status} /> },
              { key: "risk", label: "Risk", render: (b) => <RiskBadge state={b.risk?.state} reason={b.risk?.reason} /> },
              { key: "equity", label: "Equity", align: "right", render: (b) => money(b.equity) },
              { key: "pnl_pct", label: "P&L", align: "right", render: (b) => <span className={upDown(b.pnl_pct)}>{pct(b.pnl_pct, 2, true)}</span> },
              {
                key: "actions",
                label: "",
                render: (b) => (
                  <span onClick={(e) => e.stopPropagation()}>
                    {b.status === "running" || b.status === "starting" ? (
                      <button type="button" className="btn sm" onClick={() => act.mutate({ id: b.id, action: "stop" })}>{Icon.stop(12)} Stop</button>
                    ) : (
                      <button type="button" className="btn sm" onClick={() => act.mutate({ id: b.id, action: "start" })}>{Icon.play(12)} Start</button>
                    )}
                  </span>
                ),
              },
            ]}
          />
        </Card>
        {strategies.data && <NewBotForm strategies={strategies.data} onCreated={(id) => { qc.invalidateQueries({ queryKey: ["bots"] }); nav(`/bots/${id}`); }} />}
      </div>
    </>
  );
}
