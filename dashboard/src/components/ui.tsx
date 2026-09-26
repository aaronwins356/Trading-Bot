import { type ReactNode, useEffect, useState } from "react";

/* ------------------------------------------------------------------ icons */
const P = { fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" } as const;
export const Icon = {
  check: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M20 6 9 17l-5-5" /></svg>),
  x: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M18 6 6 18M6 6l12 12" /></svg>),
  alert: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 9v4M12 17h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" /></svg>),
  pause: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M10 4H6v16h4zM18 4h-4v16h4z" /></svg>),
  stop: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><rect x="5" y="5" width="14" height="14" rx="2" /></svg>),
  play: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="m6 4 14 8-14 8z" /></svg>),
  dot: (s = 8) => (<svg width={s} height={s} viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="currentColor" /></svg>),
  info: (s = 12) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><circle cx="12" cy="12" r="9" /><path d="M12 16v-4M12 8h.01" /></svg>),
  home: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M3 11 12 3l9 8v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" /></svg>),
  bot: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><rect x="4" y="8" width="16" height="12" rx="3" /><path d="M12 4v4M8.5 14h.01M15.5 14h.01" /></svg>),
  flask: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M9 3h6M10 3v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4a1.5 1.5 0 0 0 1.3-2L14 9V3" /></svg>),
  chart: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M3 3v18h18M7 15l4-4 3 3 5-6" /></svg>),
  brain: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 5a3 3 0 1 0-5.9.8A3 3 0 0 0 4 11a3 3 0 0 0 2 5.2A3 3 0 0 0 12 18zM12 5a3 3 0 1 1 5.9.8A3 3 0 0 1 20 11a3 3 0 0 1-2 5.2A3 3 0 0 1 12 18z" /></svg>),
  db: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></svg>),
  shield: (s = 16) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z" /></svg>),
  sun: (s = 14) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>),
  moon: (s = 14) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>),
  plus: (s = 14) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><path d="M12 5v14M5 12h14" /></svg>),
  table: (s = 14) => (<svg width={s} height={s} viewBox="0 0 24 24" {...P}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16" /></svg>),
};

/* ------------------------------------------------------------------ status badges */
export type Tone = "good" | "warning" | "serious" | "critical" | "info" | "neutral";
const TONE_ICON: Record<Tone, () => ReactNode> = {
  good: () => Icon.check(12),
  warning: () => Icon.alert(12),
  serious: () => Icon.alert(12),
  critical: () => Icon.x(12),
  info: () => Icon.info(12),
  neutral: () => Icon.dot(8),
};

export function Badge({ tone, children, title, pulse }: { tone: Tone; children: ReactNode; title?: string; pulse?: boolean }) {
  return (
    <span className={`badge ${tone}`} title={title}>
      <span className={`icon${pulse ? " pulse" : ""}`}>{TONE_ICON[tone]()}</span>
      {children}
    </span>
  );
}

export function BotStatusBadge({ status }: { status: string }) {
  const map: Record<string, [Tone, string]> = {
    running: ["good", "Running"],
    starting: ["warning", "Starting"],
    stopped: ["neutral", "Stopped"],
    finished: ["info", "Finished"],
    error: ["critical", "Error"],
  };
  const [tone, label] = map[status] ?? ["neutral", status];
  return <Badge tone={tone} pulse={status === "running"}>{label}</Badge>;
}

export function RiskBadge({ state, reason }: { state?: string; reason?: string }) {
  const s = state || "running";
  const map: Record<string, [Tone, string]> = { running: ["good", "Armed"], paused: ["warning", "Paused"], halted: ["critical", "Halted"] };
  const [tone, label] = map[s] ?? ["neutral", s];
  return <Badge tone={tone} title={reason || undefined}>{label}</Badge>;
}

export function VerdictBadge({ verdict }: { verdict: string }) {
  const map: Record<string, [Tone, string]> = {
    recommended: ["good", "Recommended"],
    conditional: ["warning", "Conditional"],
    "not-recommended": ["critical", "Not recommended"],
    ai: ["info", "AI"],
    experimental: ["neutral", "Experimental"],
  };
  const [tone, label] = map[verdict] ?? ["neutral", verdict];
  return <Badge tone={tone}>{label}</Badge>;
}

export function ModeBadge({ mode }: { mode: string }) {
  const map: Record<string, [Tone, string]> = { live: ["serious", "Live"], paper: ["info", "Paper"], replay: ["neutral", "Replay"] };
  const [tone, label] = map[mode] ?? ["neutral", mode];
  return <Badge tone={tone}>{label}</Badge>;
}

/* ------------------------------------------------------------------ layout pieces */
export function Card({ title, sub, actions, children, flush, className }: { title?: ReactNode; sub?: ReactNode; actions?: ReactNode; children: ReactNode; flush?: boolean; className?: string }) {
  return (
    <section className={`card ${className ?? ""}`}>
      {(title || actions) && (
        <div className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {sub && <div className="sub">{sub}</div>}
          </div>
          {actions && <div className="row">{actions}</div>}
        </div>
      )}
      <div className={`card-body${flush ? " flush" : ""}`}>{children}</div>
    </section>
  );
}

export function StatTile({ label, value, delta, deltaClass, hint }: { label: string; value: ReactNode; delta?: ReactNode; deltaClass?: string; hint?: string }) {
  return (
    <div className="card tile" title={hint}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {delta !== undefined && <div className={`tile-delta ${deltaClass ?? "secondary"}`}>{delta}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function PageHead({ title, sub, actions }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label?: string }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" className={o.value === value ? "on" : ""} aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ confirmation */
export function ConfirmButton({
  children,
  onConfirm,
  title,
  message,
  confirmLabel = "Confirm",
  className = "btn",
  danger,
  disabled,
}: {
  children: ReactNode;
  onConfirm: () => unknown;
  title: string;
  message: ReactNode;
  confirmLabel?: string;
  className?: string;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  return (
    <>
      <button type="button" className={className} disabled={disabled} onClick={() => setOpen(true)}>
        {children}
      </button>
      {open && (
        <div className="modal-back" onClick={() => setOpen(false)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
            <h2>{title}</h2>
            <div className="secondary">{message}</div>
            <div className="row" style={{ justifyContent: "flex-end" }}>
              <button type="button" className="btn" onClick={() => setOpen(false)}>Cancel</button>
              <button
                type="button"
                className={`btn ${danger ? "danger solid" : "primary"}`}
                disabled={busy}
                autoFocus
                onClick={async () => {
                  setBusy(true);
                  try {
                    await onConfirm();
                  } finally {
                    setBusy(false);
                    setOpen(false);
                  }
                }}
              >
                {confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="notice danger">{msg}</div>;
}
