import { useMutation, useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, type Decision, type JevSettings, post } from "../api";
import { DataTable } from "../components/DataTable";
import { Badge, Card, ErrorNote, PageHead, StatTile } from "../components/ui";
import { dateTime, num, pct, price } from "../format";

interface Status {
  defaults: JevSettings;
  endpoint: { ok: boolean; error?: string; models?: string[]; model_available?: boolean; base_url: string; model: string };
  bots: { id: string; name: string; jev: JevSettings }[];
}

interface AskResult {
  symbol: string;
  timeframe: string;
  as_of: number;
  price: number;
  llm_used: boolean;
  context: {
    market_features: Record<string, number | null>;
    quant_analysts: { name: string; signal: number | null; weight: number; description: string; evidence: string }[];
    quant_consensus: { target_exposure: number; method: string };
  };
  decision: {
    consensus: number;
    final_exposure: number;
    source: string;
    note: string;
    error: string;
    latency_ms: number;
    model: string;
    llm: { action: string; target_exposure: number; confidence: number; rationale: string; key_risks: string[] } | null;
  };
}

const FLOW = [
  ["Quant analysts", "Validated signal models: TS-momentum, Turtle breakout, MA trend, 20-day breakout"],
  ["Quant consensus", "Weighted signal × volatility target → a backtestable baseline exposure"],
  ["JEV (open-weight LLM)", "gpt-oss / Qwen / Llama via Ollama or vLLM; anonymised context; strict JSON"],
  ["Authority rules", "advisory: log only · veto: may only reduce · full: decides within limits"],
  ["Risk manager", "Kill switch, drawdown & daily-loss limits, exposure caps, fat-finger checks"],
] as const;

function ExposureBar({ value }: { value: number | null }) {
  if (value === null || value === undefined || !Number.isFinite(value)) return <span className="muted">—</span>;
  const w = Math.min(Math.abs(value), 1) * 50;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <div style={{ position: "relative", width: 120, height: 10, background: "var(--surface-2)", borderRadius: 99 }} aria-hidden="true">
        <div style={{ position: "absolute", left: "50%", top: -2, bottom: -2, width: 1, background: "var(--axis)" }} />
        <div style={{ position: "absolute", top: 1, bottom: 1, borderRadius: 99, background: value >= 0 ? "var(--div-pos)" : "var(--div-neg)", left: value >= 0 ? "50%" : `${50 - w}%`, width: `${w}%` }} />
      </div>
      <span className="num">{value >= 0 ? "+" : ""}{value.toFixed(2)}</span>
    </div>
  );
}

export default function JevPage() {
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434/v1");
  const [model, setModel] = useState("gpt-oss:20b");
  const [authority, setAuthority] = useState<"advisory" | "veto" | "full">("veto");
  const [effort, setEffort] = useState<"low" | "medium" | "high">("medium");
  const [useLlm, setUseLlm] = useState(true);
  const [dataset, setDataset] = useState("bitstamp|BTC/USD|1d");
  const status = useQuery({
    queryKey: ["jev-status", baseUrl, model],
    queryFn: () => api<Status>(`/api/jev/status?base_url=${encodeURIComponent(baseUrl)}&model=${encodeURIComponent(model)}`),
    staleTime: 15_000,
  });
  const decisions = useQuery({ queryKey: ["decisions"], queryFn: () => api<Decision[]>("/api/decisions?limit=100") });
  const ask = useMutation({
    mutationFn: () => {
      const [exchange, symbol, timeframe] = dataset.split("|");
      return post<AskResult>("/api/jev/ask", {
        exchange,
        symbol,
        timeframe,
        jev: { ...(status.data?.defaults ?? {}), enabled: useLlm, base_url: baseUrl, model, authority, reasoning_effort: effort, timeout_s: 120 },
      });
    },
  });
  const ep = status.data?.endpoint;
  const r = ask.data;
  return (
    <>
      <PageHead
        title="JEV — the decision-making AI"
        sub="An open-source replacement for a closed decision AI: an open-weight model you run yourself decides target exposure on top of validated quant signals — and can never bypass the risk engine."
      />
      <div className="stack">
        <div className="grid grid-5">
          {FLOW.map(([t, d], i) => (
            <div key={t} className="card tile" style={{ position: "relative" }}>
              <div className="tile-label">Step {i + 1}</div>
              <div style={{ fontWeight: 600, marginTop: 4 }}>{t}</div>
              <div className="secondary" style={{ fontSize: 12.5, marginTop: 4 }}>{d}</div>
            </div>
          ))}
        </div>

        <div className="grid grid-2">
          <Card title="Model endpoint" sub="Any OpenAI-compatible server">
            <div className="stack">
              <div className="form-grid">
                <label className="field">Base URL<input className="input mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
                <label className="field">Model<input className="input mono" value={model} onChange={(e) => setModel(e.target.value)} /></label>
              </div>
              <div className="row">
                {status.isFetching ? (
                  <Badge tone="neutral">Checking…</Badge>
                ) : ep?.ok ? (
                  ep.model_available ? <Badge tone="good">Reachable · model loaded</Badge> : <Badge tone="warning">Reachable · model not pulled</Badge>
                ) : (
                  <Badge tone="critical">Not reachable</Badge>
                )}
                <button className="btn sm" onClick={() => status.refetch()}>Re-check</button>
              </div>
              {ep && !ep.ok && (
                <div className="notice warn">
                  No model server at <code>{ep.base_url}</code>. JEV then falls back to the quant consensus (safe by design). To run it locally:
                  <div className="pre" style={{ marginTop: 8 }}>{`# install Ollama (https://ollama.com), then:\nollama pull ${model}\nollama serve   # exposes http://localhost:11434/v1`}</div>
                  gpt-oss-20b needs ~16 GB RAM; <code>qwen3:8b</code> or <code>llama3.1:8b</code> run on smaller machines.
                </div>
              )}
              {ep?.ok && ep.models && <div className="secondary" style={{ fontSize: 12.5 }}>Served models: {ep.models.join(", ") || "none"}</div>}
            </div>
          </Card>
          <Card title="Ask JEV now" sub="One decision on the latest stored candles (no orders are placed)">
            <div className="stack">
              <div className="form-grid">
                <label className="field">Market
                  <select className="input" value={dataset} onChange={(e) => setDataset(e.target.value)}>
                    <option value="bitstamp|BTC/USD|1d">BTC/USD · daily</option>
                    <option value="bitstamp|BTC/USD|4h">BTC/USD · 4h</option>
                    <option value="coinmetrics|ETH/USD|1d">ETH/USD · daily</option>
                    <option value="coinmetrics|XRP/USD|1d">XRP/USD · daily</option>
                  </select>
                </label>
                <label className="field">Authority
                  <select className="input" value={authority} onChange={(e) => setAuthority(e.target.value as typeof authority)}>
                    <option value="veto">veto (may only reduce)</option>
                    <option value="advisory">advisory (log only)</option>
                    <option value="full">full (decides within limits)</option>
                  </select>
                </label>
                <label className="field">Reasoning effort
                  <select className="input" value={effort} onChange={(e) => setEffort(e.target.value as typeof effort)}>
                    <option>low</option><option>medium</option><option>high</option>
                  </select>
                </label>
                <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={useLlm} onChange={(e) => setUseLlm(e.target.checked)} /> Call the LLM</label>
              </div>
              <div className="row">
                <button className="btn primary" disabled={ask.isPending} onClick={() => ask.mutate()}>{ask.isPending ? "Thinking…" : "Ask JEV"}</button>
              </div>
              <ErrorNote error={ask.error} />
            </div>
          </Card>
        </div>

        {r && (
          <div className="grid grid-3">
            <StatTile label={`${r.symbol} · ${r.timeframe} close`} value={price(r.price)} delta={dateTime(r.as_of)} />
            <StatTile label="Quant consensus" value={<ExposureBar value={r.decision.consensus} />} delta="weighted analysts × vol target" />
            <StatTile
              label="JEV final exposure"
              value={<ExposureBar value={r.decision.final_exposure} />}
              delta={`${r.decision.source}${r.decision.model ? ` · ${r.decision.model}` : ""}${r.decision.latency_ms ? ` · ${r.decision.latency_ms} ms` : ""}`}
            />
          </div>
        )}
        {r && (
          <div className="grid grid-2">
            <Card title="Analyst panel" flush>
              <DataTable
                rows={r.context.quant_analysts}
                rowKey={(a) => a.name}
                columns={[
                  { key: "name", label: "Analyst", render: (a) => <div className="wrap"><b>{a.name}</b><div className="muted" style={{ fontSize: 12 }}>{a.description}</div></div> },
                  { key: "weight", label: "Weight", align: "right", render: (a) => num(a.weight) },
                  { key: "signal", label: "Signal", render: (a) => <ExposureBar value={a.signal} /> },
                ]}
              />
            </Card>
            <Card title="Decision">
              {r.decision.llm ? (
                <div className="stack">
                  <div className="row"><Badge tone="info">{r.decision.llm.action}</Badge> <span className="secondary">confidence {pct(r.decision.llm.confidence * 100, 0)} · LLM target {r.decision.llm.target_exposure.toFixed(2)}</span></div>
                  <p>{r.decision.llm.rationale}</p>
                  {r.decision.llm.key_risks.length > 0 && <ul style={{ margin: 0, paddingLeft: 18 }} className="secondary">{r.decision.llm.key_risks.map((k) => <li key={k}>{k}</li>)}</ul>}
                  {r.decision.note && <div className="notice">{r.decision.note}</div>}
                </div>
              ) : (
                <div className="stack">
                  <p className="secondary">{r.llm_used ? "The LLM did not return a usable answer, so JEV used the quant consensus." : "LLM disabled — JEV traded the quant consensus."}</p>
                  {r.decision.error && <div className="notice warn">{r.decision.error}</div>}
                </div>
              )}
              <h3 className="mt" style={{ marginBottom: 6 }}>Features the model saw (anonymised)</h3>
              <dl className="kv">
                {Object.entries(r.context.market_features).map(([k, v]) => (
                  <Fragment key={k}>
                    <dt>{k}</dt>
                    <dd>{v === null ? "—" : num(v)}</dd>
                  </Fragment>
                ))}
              </dl>
            </Card>
          </div>
        )}

        <Card title="Decision journal (all bots)" sub="Consensus vs LLM vs executed exposure" flush>
          <DataTable<Decision>
            rows={decisions.data ?? []}
            rowKey={(d, i) => `${d.bot_id}-${d.ts}-${i}`}
            maxHeight={420}
            empty="No decisions yet — create a JEVStrategy bot."
            columns={[
              { key: "ts", label: "Time", render: (d) => dateTime(d.ts) },
              { key: "bot_id", label: "Bot" },
              { key: "consensus", label: "Consensus", align: "right", render: (d) => d.consensus.toFixed(2) },
              { key: "llm_exposure", label: "LLM", align: "right", render: (d) => (d.llm_exposure === null ? "—" : d.llm_exposure.toFixed(2)) },
              { key: "final_exposure", label: "Final", align: "right", render: (d) => <b>{d.final_exposure.toFixed(2)}</b> },
              { key: "rationale", label: "Rationale", render: (d) => <span className="wrap" style={{ fontSize: 12.5 }}>{d.error ? <span className="down">fallback</span> : d.rationale || <span className="muted">consensus</span>}</span> },
            ]}
          />
        </Card>
        <div className="notice">
          <b>How to evaluate the AI honestly.</b> LLMs have memorised market history, so replaying the past overstates them even with anonymised inputs. JEV's quant consensus is
          validated by the research pipeline; judge the LLM layer only on <b>forward paper trading</b>, comparing its journal against the consensus it modified.
        </div>
      </div>
    </>
  );
}
