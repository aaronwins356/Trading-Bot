import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { api, ApiError, getToken, post, setToken } from "./api";
import { ConfirmButton, Icon } from "./components/ui";
import { useLiveEvents } from "./live";
import BacktestPage from "./pages/Backtest";
import BotDetail from "./pages/BotDetail";
import BotsPage from "./pages/Bots";
import DataPage from "./pages/Data";
import JevPage from "./pages/Jev";
import OverviewPage from "./pages/Overview";
import ResearchPage from "./pages/Research";
import StudyDetail from "./pages/StudyDetail";
import { useThemePref } from "./theme";

function TokenGate({ onDone }: { onDone: () => void }) {
  const [t, setT] = useState(getToken());
  const [err, setErr] = useState("");
  return (
    <div className="modal-back">
      <form
        className="modal"
        onSubmit={async (e) => {
          e.preventDefault();
          setToken(t.trim());
          try {
            await api("/api/auth/check");
            onDone();
          } catch {
            setErr("That token was rejected by the API.");
          }
        }}
      >
        <h2>API token required</h2>
        <p className="secondary">This server has <code>TRADEBOT_API_TOKEN</code> set. Paste the token to continue; it is stored in this browser only.</p>
        <input className="input" type="password" value={t} onChange={(e) => setT(e.target.value)} autoFocus aria-label="API token" />
        {err && <div className="notice danger">{err}</div>}
        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button className="btn primary" type="submit">Unlock</button>
        </div>
      </form>
    </div>
  );
}

const NAV = [
  { to: "/", label: "Overview", icon: Icon.home },
  { to: "/bots", label: "Bots", icon: Icon.bot },
  { to: "/research", label: "Strategy research", icon: Icon.flask },
  { to: "/backtest", label: "Backtest lab", icon: Icon.chart },
  { to: "/jev", label: "JEV decision AI", icon: Icon.brain },
  { to: "/data", label: "Market data", icon: Icon.db },
];

export default function App() {
  const qc = useQueryClient();
  const [theme, setTheme] = useThemePref();
  const [locked, setLocked] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<{ ok: boolean; version: string; auth_required: boolean }>("/api/health"), staleTime: 60_000 });
  const authCheck = useQuery({
    queryKey: ["auth"],
    queryFn: () => api("/api/auth/check"),
    enabled: !!health.data?.auth_required,
    retry: false,
  });
  const needsToken = locked || (health.data?.auth_required && authCheck.error instanceof ApiError && authCheck.error.status === 401);
  const connected = useLiveEvents();

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M3 17 9 11l4 4 8-9" /></svg>
          </span>
          Trading-Bot
        </div>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.to === "/"} className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}>
            {n.icon(16)}
            {n.label}
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="muted" style={{ fontSize: 12 }}>
              <span style={{ color: connected ? "var(--good)" : "var(--text-muted)" }}>{Icon.dot(8)}</span> {connected ? "Live" : "Offline"} · v{health.data?.version ?? "…"}
            </span>
            <button
              type="button"
              className="btn sm ghost"
              aria-label="Toggle theme"
              title={`Theme: ${theme}`}
              onClick={() => setTheme(theme === "dark" ? "light" : theme === "light" ? "system" : "dark")}
            >
              {theme === "dark" ? Icon.moon(14) : theme === "light" ? Icon.sun(14) : <span style={{ fontSize: 11 }}>Auto</span>}
            </button>
          </div>
          <ConfirmButton
            className="btn danger"
            danger
            title="Engage the global kill switch?"
            message={
              <>
                Every bot is <b>halted</b>: new entries are blocked until you re-arm each bot manually. Exits and protective stops still work. Choose
                <b> Halt + flatten</b> on a bot page to also close positions.
              </>
            }
            confirmLabel="Halt all bots"
            onConfirm={async () => {
              await post("/api/kill-switch", { reason: "global kill switch (dashboard)", flatten: false });
              qc.invalidateQueries();
            }}
          >
            {Icon.shield(14)} Kill switch
          </ConfirmButton>
          {health.data?.auth_required && (
            <button type="button" className="btn sm ghost" onClick={() => { setToken(""); setLocked(true); }}>Lock</button>
          )}
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/bots" element={<BotsPage />} />
          <Route path="/bots/:id" element={<BotDetail />} />
          <Route path="/research" element={<ResearchPage />} />
          <Route path="/research/:key" element={<StudyDetail />} />
          <Route path="/backtest" element={<BacktestPage />} />
          <Route path="/jev" element={<JevPage />} />
          <Route path="/data" element={<DataPage />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
      {needsToken && <TokenGate onDone={() => { setLocked(false); qc.invalidateQueries(); }} />}
    </div>
  );
}
