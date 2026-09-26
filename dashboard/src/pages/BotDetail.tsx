import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, type BotStatus, type Decision, del, type EventRow, type OrderRow, post, type Trade } from "../api";
import { CandleChart, type Marker, TimeSeriesChart } from "../components/charts";
import { ChartFrame, LegendItem } from "../components/ChartFrame";
import { DataTable } from "../components/DataTable";
import { BotStatusBadge, Card, ConfirmButton, ErrorNote, Icon, ModeBadge, PageHead, RiskBadge, Segmented, StatTile } from "../components/ui";
import { ago, dateTime, hours, money, pct, price, qty, signedMoney, upDown } from "../format";
import { useTokens } from "../theme";

export default function BotDetail() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const tk = useTokens(["--series-1", "--div-pos", "--div-neg", "--text-secondary"]);
  const [log, setLog] = useState<"lin" | "log">("lin");
  const bot = useQuery({ queryKey: ["bot", id], queryFn: () => api<BotStatus>(`/api/bots/${id}`), refetchInterval: 5_000 });
  const equity = useQuery({ queryKey: ["bot", id, "equity"], queryFn: () => api<{ ts: number; equity: number; price: number | null }[]>(`/api/bots/${id}/equity`) });
  const candles = useQuery({ queryKey: ["bot", id, "candles"], queryFn: () => api<{ symbol: string; timeframe: string; candles: [number, number, number, number, number][]; trades: Trade[] }>(`/api/bots/${id}/candles?limit=600`) });
  const trades = useQuery({ queryKey: ["bot", id, "trades"], queryFn: () => api<Trade[]>(`/api/bots/${id}/trades`) });
  const orders = useQuery({ queryKey: ["bot", id, "orders"], queryFn: () => api<OrderRow[]>(`/api/bots/${id}/orders?limit=200`) });
  const events = useQuery({ queryKey: ["bot", id, "events"], queryFn: () => api<EventRow[]>(`/api/bots/${id}/events?limit=150`) });
  const isJev = bot.data?.strategy === "JEVStrategy";
  const decisions = useQuery({ queryKey: ["bot", id, "decisions"], queryFn: () => api<Decision[]>(`/api/bots/${id}/decisions?limit=100`), enabled: isJev });
  const [openDecision, setOpenDecision] = useState<Decision | null>(null);

  const cmd = useMutation({
    mutationFn: async (a: { kind: "start" | "stop" | "fresh" | "command"; command?: string; arg?: string }) => {
      if (a.kind === "start") return post(`/api/bots/${id}/start`, { fresh: false });
      if (a.kind === "fresh") return post(`/api/bots/${id}/start`, { fresh: true });
      if (a.kind === "stop") return post(`/api/bots/${id}/stop`);
      return post(`/api/bots/${id}/command`, { command: a.command, arg: a.arg });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["bot", id] }),
  });

  const markers: Marker[] = useMemo(
    () =>
      (candles.data?.trades ?? []).flatMap((t) => [
        { time: t.opened_at, price: t.entry_price, kind: "entry" as const, side: t.side },
        { time: t.closed_at, price: t.exit_price, kind: "exit" as const, side: t.side, pnl: t.pnl },
      ]),
    [candles.data],
  );
  const eqSeries = useMemo(() => [{ id: "eq", label: "Equity", data: (equity.data ?? []).map((p) => [p.ts, p.equity] as [number, number]), color: tk["--series-1"], kind: "area" as const }], [equity.data, tk]);

  const b = bot.data;
  if (bot.error) return <ErrorNote error={bot.error} />;
  if (!b) return <div className="empty">Loading…</div>;
  const running = b.status === "running" || b.status === "starting";
  const risk = b.risk?.state ?? "running";
  return (
    <>
      <PageHead
        title={
          <span className="row" style={{ gap: 10 }}>
            {b.name} <BotStatusBadge status={b.status} /> <ModeBadge mode={b.mode} /> <RiskBadge state={risk} reason={b.risk?.reason} />
          </span>
        }
        sub={`${b.strategy} · ${b.symbols?.join(", ")} · ${b.timeframe}${b.last_bar_ts ? ` · last candle ${dateTime(b.last_bar_ts)}` : ""}`}
        actions={
          <>
            {running ? (
              <button className="btn" onClick={() => cmd.mutate({ kind: "stop" })}>{Icon.stop(12)} Stop</button>
            ) : (
              <>
                <button className="btn primary" onClick={() => cmd.mutate({ kind: "start" })}>{Icon.play(12)} Start</button>
                <ConfirmButton title="Restart from scratch?" message="Resets the paper account, positions and replay position for this bot. Trade history stays in the log. (A halted kill switch stays halted.)" confirmLabel="Restart fresh" onConfirm={() => cmd.mutateAsync({ kind: "fresh" })}>Restart fresh</ConfirmButton>
              </>
            )}
            {risk === "running" ? (
              <button className="btn" onClick={() => cmd.mutate({ kind: "command", command: "pause", arg: "dashboard" })}>{Icon.pause(12)} Pause entries</button>
            ) : (
              <ConfirmButton title="Re-arm this bot?" message={<>The kill switch is <b>{risk}</b>{b.risk?.reason ? ` (${b.risk.reason})` : ""}. Re-arming allows new entries again and resets the drawdown peak.</>} confirmLabel="Re-arm" onConfirm={() => cmd.mutateAsync({ kind: "command", command: "resume", arg: "dashboard" })}>
                {Icon.check(12)} Re-arm
              </ConfirmButton>
            )}
            <ConfirmButton className="btn danger" danger title="Halt this bot?" message="Blocks all new entries until you re-arm manually. Exits and protective stops keep working." confirmLabel="Halt" onConfirm={() => cmd.mutateAsync({ kind: "command", command: "halt", arg: "dashboard kill switch" })}>
              {Icon.shield(12)} Halt
            </ConfirmButton>
            <ConfirmButton className="btn danger" danger title="Flatten everything?" message="Cancels all open orders and closes every position at market." confirmLabel="Flatten now" disabled={!running} onConfirm={() => cmd.mutateAsync({ kind: "command", command: "flatten" })}>
              Flatten
            </ConfirmButton>
          </>
        }
      />
      <div className="stack">
        <ErrorNote error={cmd.error} />
        {b.error && <div className="notice danger">{b.error}</div>}
        {b.replay_progress !== undefined && b.mode === "replay" && (
          <div>
            <div className="row" style={{ justifyContent: "space-between", fontSize: 12 }}>
              <span className="muted">Replay progress</span>
              <span className="num">{pct(b.replay_progress * 100, 0)}</span>
            </div>
            <div className="bar-track"><div className="bar-fill" style={{ width: `${b.replay_progress * 100}%` }} /></div>
          </div>
        )}
        <div className="grid grid-5">
          <StatTile label="Equity" value={money(b.equity)} delta={`${signedMoney(b.pnl)} (${pct(b.pnl_pct, 2, true)})`} deltaClass={upDown(b.pnl)} />
          <StatTile label="Exposure" value={pct((b.exposure ?? 0) * 100, 0)} delta="position value / equity" />
          <StatTile label="Open positions" value={String(b.positions?.length ?? 0)} delta={`${b.open_orders?.length ?? 0} resting orders`} />
          <StatTile label="Closed trades" value={String(trades.data?.length ?? 0)} delta={trades.data?.length ? `win rate ${pct((trades.data.filter((t) => t.pnl > 0).length / trades.data.length) * 100, 0)}` : "—"} />
          <StatTile label="Candles processed" value={String(b.bars_processed ?? 0)} delta={b.fees_paid !== undefined ? `fees ${money(b.fees_paid)}` : undefined} />
        </div>

        <ChartFrame
          title={`${candles.data?.symbol ?? ""} price & trades`}
          sub={`${candles.data?.timeframe ?? ""} candles · arrows mark entries (below) and exits (above; blue = win, red = loss)`}
          legend={
            <div className="legend">
              <LegendItem color={tk["--div-pos"]} label="Up candle / winning exit" kind="rect" />
              <LegendItem color={tk["--div-neg"]} label="Down candle / losing exit" kind="rect" />
              <LegendItem color={tk["--text-secondary"]} label="Entry" kind="rect" />
            </div>
          }
          table={{ columns: ["Time", "Open", "High", "Low", "Close"], rows: (candles.data?.candles ?? []).slice(-200).reverse().map((c) => [dateTime(c[0]), price(c[1]), price(c[2]), price(c[3]), price(c[4])]) }}
        >
          <CandleChart candles={candles.data?.candles ?? []} markers={markers} height={380} />
        </ChartFrame>

        <ChartFrame
          title="Equity"
          sub="Balance plus unrealized P&L, recorded at every candle close"
          toolbar={<Segmented value={log} onChange={setLog} options={[{ value: "lin", label: "Linear" }, { value: "log", label: "Log" }]} label="Scale" />}
          table={{ columns: ["Time", "Equity"], rows: (equity.data ?? []).slice(-300).reverse().map((p) => [dateTime(p.ts), money(p.equity)]) }}
        >
          {equity.data && equity.data.length > 1 ? <TimeSeriesChart series={eqSeries} height={260} log={log === "log"} format={(v) => money(v)} /> : <div className="empty">Equity appears after the first processed candle.</div>}
        </ChartFrame>

        <div className="grid grid-2">
          <Card title="Positions" flush>
            <DataTable
              rows={b.positions ?? []}
              rowKey={(p) => p.symbol}
              empty="Flat."
              columns={[
                { key: "symbol", label: "Symbol" },
                { key: "qty", label: "Qty", align: "right", render: (p) => qty(p.qty) },
                { key: "entry_price", label: "Entry", align: "right", render: (p) => price(p.entry_price) },
                { key: "last_price", label: "Last", align: "right", render: (p) => price(p.last_price) },
                { key: "unrealized_pnl", label: "Unrealized", align: "right", render: (p) => <span className={upDown(p.unrealized_pnl)}>{signedMoney(p.unrealized_pnl)}</span> },
              ]}
            />
          </Card>
          <Card title="Recent orders" sub="Including risk-rejected attempts (audit trail)" flush>
            <DataTable
              rows={orders.data ?? []}
              rowKey={(o) => o.id}
              maxHeight={320}
              columns={[
                { key: "created_at", label: "Time", render: (o) => dateTime(o.created_at) },
                { key: "side", label: "Side" },
                { key: "type", label: "Type" },
                { key: "role", label: "Role" },
                { key: "qty", label: "Qty", align: "right", render: (o) => qty(o.qty) },
                { key: "avg_fill_price", label: "Fill", align: "right", render: (o) => (o.filled_qty ? price(o.avg_fill_price) : o.price ? `@ ${price(o.price)}` : "—") },
                { key: "status", label: "Status", render: (o) => <span className={o.status === "rejected" ? "down" : ""} title={o.reject_reason || o.tag}>{o.status}</span> },
              ]}
            />
          </Card>
        </div>

        <Card title="Closed trades" flush>
          <DataTable<Trade>
            rows={trades.data ?? []}
            rowKey={(t, i) => `${t.id}-${i}`}
            maxHeight={420}
            empty="No closed trades yet."
            columns={[
              { key: "opened_at", label: "Opened", render: (t) => dateTime(t.opened_at) },
              { key: "closed_at", label: "Closed", render: (t) => dateTime(t.closed_at) },
              { key: "side", label: "Side" },
              { key: "qty", label: "Qty", align: "right", render: (t) => qty(t.qty) },
              { key: "entry_price", label: "Entry", align: "right", render: (t) => price(t.entry_price) },
              { key: "exit_price", label: "Exit", align: "right", render: (t) => price(t.exit_price) },
              { key: "held", label: "Held", align: "right", render: (t) => hours((t.closed_at - t.opened_at) / 3.6e6), sortValue: (t) => t.closed_at - t.opened_at },
              { key: "return_pct", label: "Return", align: "right", render: (t) => <span className={upDown(t.return_pct)}>{pct(t.return_pct, 2, true)}</span> },
              { key: "pnl", label: "P&L", align: "right", render: (t) => <span className={upDown(t.pnl)}>{signedMoney(t.pnl)}</span> },
              { key: "exit_reason", label: "Exit reason", render: (t) => <span className="muted">{t.exit_reason}</span> },
            ]}
          />
        </Card>

        {isJev && (
          <Card title="JEV decision journal" sub="Every decision with its context and the raw model reply — click a row to inspect" flush>
            <DataTable<Decision>
              rows={decisions.data ?? []}
              rowKey={(d, i) => `${d.ts}-${i}`}
              maxHeight={420}
              onRowClick={setOpenDecision}
              columns={[
                { key: "ts", label: "Time", render: (d) => dateTime(d.ts) },
                { key: "consensus", label: "Quant consensus", align: "right", render: (d) => d.consensus.toFixed(2) },
                { key: "llm_exposure", label: "LLM target", align: "right", render: (d) => (d.llm_exposure === null ? "—" : d.llm_exposure.toFixed(2)) },
                { key: "final_exposure", label: "Final", align: "right", render: (d) => <b>{d.final_exposure.toFixed(2)}</b> },
                { key: "confidence", label: "Conf.", align: "right", render: (d) => (d.confidence === null ? "—" : d.confidence.toFixed(2)) },
                { key: "rationale", label: "Rationale", render: (d) => <span className="wrap" style={{ fontSize: 12.5 }}>{d.error ? <span className="down">fallback: {d.error.slice(0, 80)}</span> : d.rationale || <span className="muted">consensus</span>}</span> },
              ]}
            />
          </Card>
        )}
        {openDecision && (
          <div className="modal-back" onClick={() => setOpenDecision(null)}>
            <div className="modal" style={{ width: "min(820px, 100%)" }} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Decision detail">
              <h2>Decision at {dateTime(openDecision.ts)}</h2>
              <div className="kv">
                <dt>Model</dt><dd>{openDecision.model || "consensus only"}</dd>
                <dt>Authority</dt><dd>{openDecision.authority}</dd>
                <dt>Consensus → final</dt><dd>{openDecision.consensus.toFixed(3)} → {openDecision.final_exposure.toFixed(3)}</dd>
                <dt>Latency</dt><dd>{openDecision.latency_ms} ms</dd>
              </div>
              <h3>Context sent to the model</h3>
              <div className="pre">{JSON.stringify(openDecision.context, null, 2)}</div>
              {openDecision.raw_response && (<><h3>Raw reply</h3><div className="pre">{openDecision.raw_response}</div></>)}
              <div className="row" style={{ justifyContent: "flex-end" }}><button className="btn" onClick={() => setOpenDecision(null)}>Close</button></div>
            </div>
          </div>
        )}

        <Card title="Event log" flush>
          <DataTable<EventRow>
            rows={events.data ?? []}
            rowKey={(e, i) => `${e.ts}-${i}`}
            maxHeight={360}
            columns={[
              { key: "ts", label: "When", render: (e) => <span className="nowrap" title={dateTime(e.ts)}>{ago(e.ts)}</span> },
              { key: "level", label: "Level", render: (e) => <span className={e.level === "error" ? "down" : e.level === "warning" ? "" : "muted"}>{e.level}</span> },
              { key: "kind", label: "Kind" },
              { key: "message", label: "Message", render: (e) => <span className="wrap" style={{ fontSize: 12.5 }}>{e.message}</span> },
            ]}
          />
        </Card>

        <details>
          <summary className="secondary" style={{ cursor: "pointer" }}>Configuration</summary>
          <div className="pre mt">{JSON.stringify(b.config, null, 2)}</div>
          <div className="row mt">
            <ConfirmButton className="btn danger" danger title="Delete this bot?" message="Stops the bot and deletes its configuration, orders, trades, equity history and decisions from the database." confirmLabel="Delete bot" onConfirm={async () => { await del(`/api/bots/${id}`); qc.invalidateQueries({ queryKey: ["bots"] }); nav("/bots"); }}>
              Delete bot
            </ConfirmButton>
          </div>
        </details>
      </div>
    </>
  );
}
