import { type ReactNode, useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  label: string;
  align?: "left" | "right";
  render?: (row: T) => ReactNode;
  sortValue?: (row: T) => number | string | null | undefined;
  width?: number | string;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  empty = "Nothing here yet.",
  maxHeight,
  initialSort,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T, i: number) => string;
  onRowClick?: (row: T) => void;
  empty?: ReactNode;
  maxHeight?: number;
  initialSort?: { key: string; dir: "asc" | "desc" };
}) {
  const [sort, setSort] = useState(initialSort ?? null);
  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const val = col.sortValue ?? ((r: T) => (r as Record<string, unknown>)[col.key] as number | string);
    const dir = sort.dir === "desc" ? -1 : 1;
    const missing = (v: unknown) => v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v));
    // empty values always sink to the bottom, whatever the direction
    return [...rows].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (missing(va) || missing(vb)) return missing(va) === missing(vb) ? 0 : missing(va) ? 1 : -1;
      if (va === vb) return 0;
      return (va! < vb! ? -1 : 1) * dir;
    });
  }, [rows, sort, columns]);

  if (!rows.length) return <div className="empty">{empty}</div>;
  return (
    <div className="table-wrap" style={maxHeight ? { maxHeight, overflowY: "auto" } : undefined}>
      <table className="data">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  className={`sortable ${c.align === "right" ? "r" : ""}`}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                  onClick={() => setSort(active && sort!.dir === "desc" ? { key: c.key, dir: "asc" } : { key: c.key, dir: "desc" })}
                >
                  {c.label}
                  {active ? (sort!.dir === "desc" ? " ↓" : " ↑") : ""}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r, i) => (
            <tr key={rowKey(r, i)} className={onRowClick ? "clickable" : ""} onClick={onRowClick ? () => onRowClick(r) : undefined}>
              {columns.map((c) => (
                <td key={c.key} className={c.align === "right" ? "r" : ""}>
                  {c.render ? c.render(r) : String((r as Record<string, unknown>)[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
