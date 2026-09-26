// Number & date formatting. Big standalone numbers use proportional figures (CSS);
// tables add the .num class for tabular figures.

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export function money(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const sign = v < 0 ? "-" : "";
  const a = Math.abs(v);
  if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
  if (a >= 1e5) return `${sign}$${(a / 1e3).toFixed(1)}K`;
  return `${sign}$${a.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

export function signedMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return (v >= 0 ? "+" : "") + money(v).replace("-", "−");
}

export function pct(v: number | null | undefined, digits = 1, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const s = v.toFixed(digits);
  return `${signed && v > 0 ? "+" : ""}${s.replace("-", "−")}%`;
}

export function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return digits === 0 ? nf0.format(v) : digits === 2 ? nf2.format(v) : v.toFixed(digits);
}

export function qty(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  return v.toLocaleString("en-US", { maximumFractionDigits: a >= 100 ? 2 : a >= 1 ? 4 : 8 });
}

export function price(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  const a = Math.abs(v);
  return v.toLocaleString("en-US", { maximumFractionDigits: a >= 1000 ? 2 : a >= 1 ? 4 : 8 });
}

export function dateTime(ms: number | null | undefined): string {
  if (!ms) return "—";
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16);
}

export function date(ms: number | null | undefined): string {
  if (!ms) return "—";
  return new Date(ms).toISOString().slice(0, 10);
}

export function ago(ms: number | null | undefined): string {
  if (!ms) return "—";
  const s = (Date.now() - ms) / 1000;
  if (s < 60) return `${Math.max(0, Math.round(s))}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function hours(h: number): string {
  if (!Number.isFinite(h)) return "—";
  if (h < 48) return `${h.toFixed(1)}h`;
  return `${(h / 24).toFixed(1)}d`;
}

export function upDown(v: number | null | undefined): string {
  if (v === null || v === undefined || !Number.isFinite(v) || v === 0) return "";
  return v > 0 ? "up" : "down";
}
