import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  type SeriesMarker,
  type SeriesType,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { dateTime } from "../format";
import { useTokens } from "../theme";

const CHROME = ["--surface-1", "--text-muted", "--text-secondary", "--grid", "--axis", "--div-pos", "--div-neg"];

export function alpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const n = parseInt(h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

const sec = (ms: number) => Math.floor(ms / 1000) as UTCTimestamp;

function clean(points: [number, number][]): { time: UTCTimestamp; value: number }[] {
  const m = new Map<number, number>();
  for (const [t, v] of points) if (Number.isFinite(v)) m.set(sec(t), v);
  return [...m.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time: time as UTCTimestamp, value }));
}

export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(v / 1e6).toFixed(a >= 1e7 ? 0 : 1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}K`;
  if (a >= 1e3) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (a >= 1) return v.toFixed(a >= 100 ? 0 : 2);
  return v.toPrecision(3);
}

function baseOptions(tk: Record<string, string>, height: number, log: boolean, axisFormat: (v: number) => string = compact) {
  return {
    height,
    localization: { priceFormatter: axisFormat },
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: tk["--surface-1"] },
      textColor: tk["--text-muted"],
      fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif",
      fontSize: 11,
      attributionLogo: true, // TradingView lightweight-charts attribution (Apache-2.0 NOTICE)
    },
    grid: { vertLines: { visible: false }, horzLines: { color: tk["--grid"], style: LineStyle.Solid } },
    rightPriceScale: { borderColor: tk["--axis"], mode: log ? PriceScaleMode.Logarithmic : PriceScaleMode.Normal },
    timeScale: { borderColor: tk["--axis"], timeVisible: true, secondsVisible: false },
    crosshair: {
      mode: CrosshairMode.Magnet,
      vertLine: { color: tk["--text-muted"], width: 1 as const, style: LineStyle.Solid, labelBackgroundColor: tk["--axis"] },
      horzLine: { visible: false, labelVisible: false },
    },
    handleScroll: { mouseWheel: false, pressedMouseMove: true },
    handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
  };
}

/* ------------------------------------------------------------------ tooltip helper */
interface TipRow {
  color: string;
  label: string;
  value: string;
}

function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; time: string; rows: TipRow[] } | null>(null);
  const node = tip ? (
    <div className="chart-tooltip" style={{ left: tip.x, top: tip.y }} role="status">
      <div className="t-time">{tip.time}</div>
      {tip.rows.map((r) => (
        <div className="t-row" key={r.label}>
          <span className="k">
            <span className="key-line" style={{ background: r.color }} />
            {r.label}
          </span>
          <span className="v">{r.value}</span>
        </div>
      ))}
    </div>
  ) : null;
  return { setTip, node };
}

function place(x: number, y: number, width: number): { x: number; y: number } {
  const w = 190;
  return { x: x + 16 + w > width ? Math.max(4, x - w - 16) : x + 16, y: Math.max(4, y - 20) };
}

/* ------------------------------------------------------------------ time series */
export interface TSeries {
  id: string;
  label: string;
  data: [number, number][];
  color: string;
  kind?: "line" | "area";
}

export function TimeSeriesChart({
  series,
  height = 280,
  log = false,
  format = (v: number) => v.toFixed(2),
  axisFormat,
}: {
  series: TSeries[];
  height?: number;
  log?: boolean;
  format?: (v: number) => string;
  axisFormat?: (v: number) => string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tk = useTokens(CHROME);
  const { setTip, node } = useTooltip();
  const fmt = useRef(format);
  fmt.current = format;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const chart: IChartApi = createChart(el, baseOptions(tk, height, log, axisFormat ?? compact));
    const apis: { s: TSeries; api: ISeriesApi<SeriesType> }[] = [];
    for (const s of series) {
      const common = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerRadius: 4, crosshairMarkerBorderColor: tk["--surface-1"], crosshairMarkerBorderWidth: 2 };
      const api =
        s.kind === "area"
          ? chart.addSeries(AreaSeries, { ...common, lineColor: s.color, lineWidth: 2, topColor: alpha(s.color, 0.12), bottomColor: alpha(s.color, 0.02) })
          : chart.addSeries(LineSeries, { ...common, color: s.color, lineWidth: 2 });
      api.setData(clean(s.data));
      apis.push({ s, api });
    }
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      if (!param.point || param.time === undefined || param.point.x < 0) {
        setTip(null);
        return;
      }
      const rows: TipRow[] = [];
      for (const { s, api } of apis) {
        const d = param.seriesData.get(api) as { value?: number } | undefined;
        if (d && d.value !== undefined) rows.push({ color: s.color, label: s.label, value: fmt.current(d.value) });
      }
      if (!rows.length) {
        setTip(null);
        return;
      }
      const p = place(param.point.x, param.point.y, el.clientWidth);
      setTip({ ...p, time: dateTime((param.time as number) * 1000), rows });
    });
    return () => chart.remove();
  }, [series, log, tk, height, setTip, axisFormat]);

  return (
    <div className="chart" style={{ height }} onMouseLeave={() => setTip(null)}>
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      {node}
    </div>
  );
}

/* ------------------------------------------------------------------ candles + trade markers */
export interface Marker {
  time: number;
  price: number;
  kind: "entry" | "exit";
  side: string;
  pnl?: number;
}

export function CandleChart({
  candles,
  markers = [],
  height = 360,
  log = false,
}: {
  candles: [number, number, number, number, number][];
  markers?: Marker[];
  height?: number;
  log?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const tk = useTokens(CHROME);
  const { setTip, node } = useTooltip();
  useEffect(() => {
    const el = ref.current;
    if (!el || !candles.length) return;
    // long histories span 10x+ in price: a log scale keeps early years readable
    const lows = candles.map((c) => c[3]).filter((v) => v > 0);
    const autoLog = log || (lows.length > 0 && Math.max(...candles.map((c) => c[2])) / Math.min(...lows) > 4);
    const chart = createChart(el, baseOptions(tk, height, autoLog));
    const up = tk["--div-pos"];
    const down = tk["--div-neg"];
    const s = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderVisible: false,
      wickUpColor: up,
      wickDownColor: down,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const seen = new Map<number, { time: UTCTimestamp; open: number; high: number; low: number; close: number }>();
    for (const [t, o, h, l, c] of candles) seen.set(sec(t), { time: sec(t), open: o, high: h, low: l, close: c });
    const data = [...seen.values()].sort((a, b) => a.time - b.time);
    s.setData(data);
    // snap each marker to the candle that contains it
    const times = data.map((d) => d.time as number);
    const snap = (ms: number): UTCTimestamp | null => {
      const t = ms / 1000;
      let lo = 0;
      let hi = times.length - 1;
      if (!times.length || t < times[0]) return null;
      while (lo < hi) {
        const mid = (lo + hi + 1) >> 1;
        if (times[mid] <= t) lo = mid;
        else hi = mid - 1;
      }
      return times[lo] as UTCTimestamp;
    };
    const ms: SeriesMarker<Time>[] = [];
    for (const m of markers.slice(-600)) {
      const t = snap(m.time);
      if (t === null) continue;
      const win = (m.pnl ?? 0) > 0;
      ms.push(
        m.kind === "entry"
          ? { time: t, position: "belowBar", color: tk["--text-secondary"], shape: "arrowUp", size: 0.8 }
          : { time: t, position: "aboveBar", color: win ? up : down, shape: "arrowDown", size: 0.8 },
      );
    }
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(s, ms);
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((param) => {
      if (!param.point || param.time === undefined) {
        setTip(null);
        return;
      }
      const d = param.seriesData.get(s) as { open: number; high: number; low: number; close: number } | undefined;
      if (!d) {
        setTip(null);
        return;
      }
      const f = (v: number) => v.toLocaleString("en-US", { maximumFractionDigits: v >= 1000 ? 0 : 4 });
      const chg = ((d.close / d.open - 1) * 100).toFixed(2);
      const p = place(param.point.x, param.point.y, el.clientWidth);
      setTip({
        ...p,
        time: dateTime((param.time as number) * 1000),
        rows: [
          { color: d.close >= d.open ? up : down, label: "Close", value: `${f(d.close)} (${chg}%)` },
          { color: tk["--text-muted"], label: "Open", value: f(d.open) },
          { color: tk["--text-muted"], label: "High", value: f(d.high) },
          { color: tk["--text-muted"], label: "Low", value: f(d.low) },
        ],
      });
    });
    return () => chart.remove();
  }, [candles, markers, tk, height, log, setTip]);
  if (!candles.length) return <div className="empty">No candles.</div>;
  return (
    <div className="chart" style={{ height }} onMouseLeave={() => setTip(null)}>
      <div ref={ref} style={{ position: "absolute", inset: 0 }} />
      {node}
    </div>
  );
}

/* ------------------------------------------------------------------ grouped bar chart (SVG) */
export interface BarSeries {
  label: string;
  color: string;
  values: (number | null)[];
}

export function BarChart({
  categories,
  series,
  height = 240,
  format = (v: number) => `${v.toFixed(0)}%`,
  clampPct,
}: {
  categories: string[];
  series: BarSeries[];
  height?: number;
  format?: (v: number) => string;
  clampPct?: number; // visually cap extreme bars (value still shown in tooltip/table)
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(600);
  const [hover, setHover] = useState<{ ci: number; si: number; x: number; y: number } | null>(null);
  useEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    setWidth(el.clientWidth);
    return () => ro.disconnect();
  }, []);
  const pad = { l: 44, r: 8, t: 10, b: 26 };
  const vals = series.flatMap((s) => s.values.filter((v): v is number => v !== null && Number.isFinite(v)));
  const cap = (v: number) => (clampPct ? Math.max(-clampPct, Math.min(clampPct, v)) : v);
  const maxV = Math.max(0, ...vals.map(cap));
  const minV = Math.min(0, ...vals.map(cap));
  const ticks = useMemo(() => niceTicks(minV, maxV, 4), [minV, maxV]);
  const top = ticks[ticks.length - 1];
  const bot = ticks[0];
  const plotH = height - pad.t - pad.b;
  const y = (v: number) => pad.t + ((top - v) / (top - bot || 1)) * plotH;
  const band = (width - pad.l - pad.r) / Math.max(categories.length, 1);
  const barW = Math.max(3, Math.min(24, (band * 0.7 - 2 * (series.length - 1)) / series.length));
  const groupW = barW * series.length + 2 * (series.length - 1);
  const everyNth = Math.ceil(categories.length / Math.max(1, Math.floor((width - pad.l) / 42)));
  return (
    <div ref={wrap} className="chart" style={{ height }} onMouseLeave={() => setHover(null)}>
      <svg width={width} height={height} role="img" aria-label="bar chart">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="var(--grid)" strokeWidth={1} />
            <text x={pad.l - 6} y={y(t)} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--text-muted)" style={{ fontVariantNumeric: "tabular-nums" }}>
              {format(t)}
            </text>
          </g>
        ))}
        <line x1={pad.l} x2={width - pad.r} y1={y(0)} y2={y(0)} stroke="var(--axis)" strokeWidth={1} />
        {categories.map((c, ci) => {
          const gx = pad.l + ci * band + (band - groupW) / 2;
          return (
            <g key={c}>
              {series.map((s, si) => {
                const v = s.values[ci];
                if (v === null || v === undefined || !Number.isFinite(v)) return null;
                const x = gx + si * (barW + 2);
                const y0 = y(0);
                const y1 = y(cap(v));
                const h = Math.max(1, Math.abs(y1 - y0));
                const r = Math.min(4, barW / 2, h);
                const isHover = hover?.ci === ci && hover?.si === si;
                const d = v >= 0 ? roundedTop(x, y1, barW, h, r) : roundedBottom(x, y0, barW, h, r);
                return (
                  <g key={si}>
                    <path d={d} fill={s.color} opacity={hover && !isHover ? 0.55 : 1} />
                    <rect
                      x={x - 2}
                      y={pad.t}
                      width={barW + 4}
                      height={plotH}
                      fill="transparent"
                      tabIndex={0}
                      aria-label={`${c} ${s.label}: ${format(v)}`}
                      onMouseMove={(e) => setHover({ ci, si, x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY })}
                      onFocus={() => setHover({ ci, si, x: x + barW, y: y1 })}
                      onBlur={() => setHover(null)}
                    />
                  </g>
                );
              })}
              {ci % everyNth === 0 && (
                <text x={pad.l + ci * band + band / 2} y={height - 8} textAnchor="middle" fontSize={11} fill="var(--text-muted)">
                  {c}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {hover && (
        <div className="chart-tooltip" style={{ left: Math.min(hover.x + 14, width - 180), top: Math.max(4, hover.y - 40) }}>
          <div className="t-time">{categories[hover.ci]}</div>
          {series.map((s) => (
            <div className="t-row" key={s.label}>
              <span className="k"><span className="key-rect" style={{ background: s.color }} />{s.label}</span>
              <span className="v">{s.values[hover.ci] === null || s.values[hover.ci] === undefined ? "—" : format(s.values[hover.ci] as number)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function roundedTop(x: number, y: number, w: number, h: number, r: number) {
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}
function roundedBottom(x: number, y: number, w: number, h: number, r: number) {
  return `M${x},${y} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w - r} Q${x + w},${y + h} ${x + w},${y + h - r} V${y} Z`;
}

export function niceTicks(min: number, max: number, count: number): number[] {
  if (min === max) {
    max = min + 1;
  }
  const span = max - min;
  const step0 = span / count;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const norm = step0 / mag;
  const step = (norm >= 5 ? 10 : norm >= 2 ? 5 : norm >= 1 ? 2 : 1) * mag;
  const lo = Math.floor(min / step) * step;
  const hi = Math.ceil(max / step) * step;
  const out = [];
  for (let v = lo; v <= hi + step / 2; v += step) out.push(Math.round(v / step) * step);
  return out;
}

/* ------------------------------------------------------------------ heatmap (HTML grid) */
export function Heatmap({
  rows,
  cols,
  values,
  mode,
  format,
  domain,
  highlight,
  corner,
}: {
  rows: string[];
  cols: string[];
  values: (number | null)[][];
  mode: "diverging" | "sequential";
  format: (v: number) => string;
  domain?: [number, number];
  highlight?: [number, number];
  corner?: ReactNode;
}) {
  const flat = values.flat().filter((v): v is number => v !== null && Number.isFinite(v));
  const lo = domain ? domain[0] : Math.min(...flat);
  const hi = domain ? domain[1] : Math.max(...flat);
  const absMax = Math.max(Math.abs(lo), Math.abs(hi)) || 1;
  const dark = document.documentElement.getAttribute("data-theme") === "dark" || (!document.documentElement.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);
  const cellStyle = (v: number | null): React.CSSProperties => {
    if (v === null || !Number.isFinite(v)) return { background: "transparent", color: "var(--text-muted)" };
    let bg: string;
    let t: number;
    if (mode === "diverging") {
      t = Math.min(1, Math.abs(v) / absMax);
      const pole = v >= 0 ? "var(--div-pos)" : "var(--div-neg)";
      bg = `color-mix(in oklab, ${pole} ${Math.round(12 + t * 80)}%, var(--div-mid))`;
    } else {
      t = hi > lo ? (v - lo) / (hi - lo) : 0.5;
      bg = `color-mix(in oklab, var(--seq-hi) ${Math.round(t * 100)}%, var(--seq-lo))`;
    }
    const light = dark ? (mode === "sequential" && t > 0.6) : t > 0.55;
    return { background: bg, color: dark ? (light ? "#0b0b0b" : "#ffffff") : light ? "#ffffff" : "#0b0b0b" };
  };
  return (
    <div className="table-wrap">
      <table className="data heat">
        <thead>
          <tr>
            <th>{corner}</th>
            {cols.map((c) => <th key={c}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, ri) => (
            <tr key={r}>
              <th style={{ textAlign: "left", position: "static" }}>{r}</th>
              {cols.map((c, ci) => {
                const v = values[ri]?.[ci] ?? null;
                const hl = highlight && highlight[0] === ri && highlight[1] === ci;
                return (
                  <td key={c} className="cell" style={{ ...cellStyle(v), outline: hl ? "2px solid var(--text-primary)" : undefined, outlineOffset: -2 }} title={v === null ? "" : `${r} · ${c}: ${format(v)}`}>
                    {v === null ? "" : format(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* ------------------------------------------------------------------ sparkline */
export function Sparkline({ values, width = 120, height = 32 }: { values: number[]; width?: number; height?: number }) {
  if (values.length < 2) return <span className="muted">—</span>;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (i: number) => (i / (values.length - 1)) * (width - 6) + 3;
  const y = (v: number) => height - 3 - ((v - lo) / (hi - lo || 1)) * (height - 6);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  return (
    <svg width={width} height={height} aria-hidden="true">
      <path d={d} fill="none" stroke="var(--series-muted)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle cx={x(values.length - 1)} cy={y(last)} r={3} fill="var(--accent)" stroke="var(--surface-1)" strokeWidth={2} />
    </svg>
  );
}
