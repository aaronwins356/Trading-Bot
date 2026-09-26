import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { api, type BotStatus, type Overview } from "../api";
import { Sparkline } from "../components/charts";
import { DataTable } from "../components/DataTable";
import { BotStatusBadge, Card, Empty, ErrorNote, ModeBadge, PageHead, RiskBadge, StatTile } from "../components/ui";
import { ago, dateTime, money, pct, price, qty, signedMoney, upDown } from "../format";

function BotSpark({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["bot", id, "spark"], queryFn: () => api<{ ts: number; equity: number }[]>(`/api/bots/${id}/equity?max_points=80`) });
  return <Sparkline values={(q.data ?? []).map((p) => p.equity)} />;
}

export default function OverviewPage() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ["overview"], queryFn: () => api<Overview>("/api/overview"), refetchInterval: 15_000 });
  const o = q.data;
  const halted = o ? Object.values(o.risk_states).filter((s) => s === "halted").length : 0;
  return (
    <>
      <PageHead
        title="Overview"
        sub="Every bot at a glance. Paper-trade first; go live only after a strategy passes research and a few weeks of paper trading."
        actions={<Link className="btn primary" to="/bots">New bot</Link>}
      />
      <ErrorNote error={q.error} />
      {o && (
        <div className="stack">
          <div className="grid grid-4">
            <div className="card tile span-2">
              <div className="tile-label">Total equity across bots</div>
              <div className="hero" style={{ marginTop: 6 }}>{money(o.equity)}</div>
              <div className={`tile-delta ${upDown(o.pnl)}`} style={{ fontSize: 14, marginTop: 6 }}>
                {signedMoney(o.pnl)} ({pct(o.pnl_pct, 2, true)}) vs {money(o.capital, 0)} allocated
              </div>
            </div>
            <StatTile label="Bots running" value={`${o.bots_running} / ${o.bots_total}`} delta={o.bots_total ? "paper, replay & live" : "create one to start"} />
            <StatTile
              label="Kill switch"
              value={halted ? `${halted} halted` : "Armed"}
              delta={halted ? "re-arm on the bot page" : "all bots accepting entries"}
              deltaClass={halted ? "down" : "secondary"}
            />
          </div>

          {!o.bots.length ? (
            <Card title="Get started">
              <div className="stack">
                <p className="secondary">
                  No bots yet. Create one on the <Link to="/bots">Bots</Link> page — start with <b>Replay</b> mode (streams stored history as if live, no
                  exchange needed) or <b>Paper</b> mode (live exchange prices, simulated fills). Check <Link to="/research">Strategy research</Link> to see which
                  strategies survived validation.
                </p>
              </div>
            </Card>
          ) : (
            <Card title="Bots" sub="Click a row for charts, orders, trades and controls" flush>
              <DataTable<BotStatus>
                rows={o.bots}
                rowKey={(b) => b.id}
                onRowClick={(b) => nav(`/bots/${b.id}`)}
                columns={[
                  { key: "name", label: "Bot", render: (b) => <div className="wrap"><b>{b.name}</b><div className="muted" style={{ fontSize: 12 }}>{b.strategy} · {b.symbols?.join(", ")} · {b.timeframe}</div></div> },
                  { key: "status", label: "Status", render: (b) => <BotStatusBadge status={b.status} /> },
                  { key: "mode", label: "Mode", render: (b) => <ModeBadge mode={b.mode} /> },
                  { key: "risk", label: "Risk", render: (b) => <RiskBadge state={b.risk?.state} reason={b.risk?.reason} />, sortValue: (b) => b.risk?.state },
                  { key: "spark", label: "Equity trend", render: (b) => <BotSpark id={b.id} /> },
                  { key: "equity", label: "Equity", align: "right", render: (b) => money(b.equity) },
                  { key: "pnl_pct", label: "P&L", align: "right", render: (b) => <span className={upDown(b.pnl_pct)}>{pct(b.pnl_pct, 2, true)}</span> },
                ]}
              />
            </Card>
          )}

          <div className="grid grid-2">
            <Card title="Open positions" flush>
              <DataTable
                rows={o.positions}
                rowKey={(p, i) => `${p.bot_id}-${p.symbol}-${i}`}
                empty="Flat — no open positions."
                columns={[
                  { key: "bot", label: "Bot", render: (p) => <Link to={`/bots/${p.bot_id}`}>{p.bot}</Link> },
                  { key: "symbol", label: "Symbol" },
                  { key: "qty", label: "Qty", align: "right", render: (p) => qty(p.qty) },
                  { key: "entry_price", label: "Entry", align: "right", render: (p) => price(p.entry_price) },
                  { key: "last_price", label: "Last", align: "right", render: (p) => price(p.last_price) },
                ]}
              />
            </Card>
            <Card title="Recent trades" flush>
              <DataTable
                rows={o.recent_trades}
                rowKey={(t, i) => `${t.bot_id}-${t.id}-${i}`}
                empty="No closed trades yet."
                columns={[
                  { key: "closed_at", label: "Closed", render: (t) => dateTime(t.closed_at) },
                  { key: "symbol", label: "Symbol" },
                  { key: "side", label: "Side" },
                  { key: "return_pct", label: "Return", align: "right", render: (t) => <span className={upDown(t.return_pct)}>{pct(t.return_pct, 2, true)}</span> },
                  { key: "pnl", label: "P&L", align: "right", render: (t) => <span className={upDown(t.pnl)}>{signedMoney(t.pnl)}</span> },
                ]}
              />
            </Card>
          </div>

          <div className="grid grid-2">
            <Card title="Latest JEV decisions" sub="Open-source LLM decision engine — quant consensus vs final exposure" flush>
              {o.recent_decisions.length ? (
                <DataTable
                  rows={o.recent_decisions}
                  rowKey={(d, i) => `${d.bot_id}-${d.ts}-${i}`}
                  columns={[
                    { key: "ts", label: "Time", render: (d) => dateTime(d.ts) },
                    { key: "consensus", label: "Consensus", align: "right", render: (d) => d.consensus.toFixed(2) },
                    { key: "final_exposure", label: "Final", align: "right", render: (d) => <b>{d.final_exposure.toFixed(2)}</b> },
                    { key: "model", label: "Model", render: (d) => <span className="muted">{d.model || "—"}</span> },
                  ]}
                />
              ) : (
                <Empty>No JEV bots yet. See the <Link to="/jev">JEV page</Link>.</Empty>
              )}
            </Card>
            <Card title="Event log" flush>
              <DataTable
                rows={o.recent_events}
                rowKey={(e, i) => `${e.ts}-${i}`}
                empty="Quiet."
                columns={[
                  { key: "ts", label: "When", render: (e) => <span className="nowrap">{ago(e.ts)}</span> },
                  { key: "level", label: "Level", render: (e) => <span className={e.level === "error" ? "down" : e.level === "warning" ? "" : "muted"}>{e.level}</span> },
                  { key: "message", label: "Message", render: (e) => <span className="wrap" style={{ fontSize: 12.5 }}>{e.message}</span> },
                ]}
              />
            </Card>
          </div>
          {!o.notifications.length && (
            <div className="notice">
              Notifications are off. Set <code>TELEGRAM_BOT_TOKEN</code> + <code>TELEGRAM_CHAT_ID</code> or <code>DISCORD_WEBHOOK_URL</code> in the server environment to get trade and risk alerts.
            </div>
          )}
        </div>
      )}
    </>
  );
}
