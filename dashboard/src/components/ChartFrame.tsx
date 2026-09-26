import { type ReactNode, useState } from "react";
import { Icon } from "./ui";

export interface TableView {
  columns: string[];
  rows: (string | number)[][];
}

/** A chart card: title, legend, toolbar, and a table-view twin (the accessible equivalent). */
export function ChartFrame({
  title,
  sub,
  legend,
  toolbar,
  table,
  caption,
  children,
}: {
  title: ReactNode;
  sub?: ReactNode;
  legend?: ReactNode;
  toolbar?: ReactNode;
  table?: TableView;
  caption?: ReactNode;
  children: ReactNode;
}) {
  const [showTable, setShowTable] = useState(false);
  return (
    <figure className="card">
      <div className="card-head">
        <div>
          <h2>{title}</h2>
          {sub && <div className="sub">{sub}</div>}
        </div>
        <div className="row">
          {toolbar}
          {table && (
            <button type="button" className="btn sm ghost" aria-pressed={showTable} onClick={() => setShowTable((s) => !s)} title="Toggle table view">
              {Icon.table(14)} {showTable ? "Chart" : "Table"}
            </button>
          )}
        </div>
      </div>
      {legend && <div className="chart-toolbar" style={{ paddingTop: 10 }}>{legend}</div>}
      <div className="card-body flush" style={{ padding: "8px 8px 12px" }}>
        {showTable && table ? (
          <div className="table-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
            <table className="data">
              <thead>
                <tr>{table.columns.map((c, i) => <th key={c} className={i ? "r" : ""}>{c}</th>)}</tr>
              </thead>
              <tbody>
                {table.rows.map((r, i) => (
                  <tr key={i}>{r.map((v, j) => <td key={j} className={j ? "r" : ""}>{v}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          children
        )}
      </div>
      {caption && <figcaption>{caption}</figcaption>}
    </figure>
  );
}

export function LegendItem({ color, label, kind = "line" }: { color: string; label: string; kind?: "line" | "rect" }) {
  return (
    <span className="item">
      <span className={kind === "line" ? "key-line" : "key-rect"} style={{ background: color }} />
      {label}
    </span>
  );
}
