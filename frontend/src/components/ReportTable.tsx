import { useMemo } from "react";
import { delayColor } from "../styles/tokens";
import { useRouteNames } from "../api/useRouteNames";
import { useParams } from "react-router-dom";

type Schema = {
  /** Column index in the row tuple */
  index: number;
  label: string;
  align?: "left" | "right";
  /** When set, draws an inline bar; ``barColor`` is in delay-min space. */
  bar?: "delay" | "pct" | "raw";
  format?: (v: unknown) => string;
};

const ROUTE_COL: Schema = { index: 0, label: "系統", align: "left" };

const SCHEMAS: Record<string, Schema[]> = {
  ranking: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "平均", align: "right", bar: "delay", format: (v) => fmtMin(v) },
    { index: 3, label: "中央値", align: "right", format: (v) => fmtMin(v) },
    { index: 4, label: "p90", align: "right", format: (v) => fmtMin(v) },
    { index: 5, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
  ranking_best: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "平均", align: "right", bar: "delay", format: (v) => fmtMin(v) },
    { index: 3, label: "中央値", align: "right", format: (v) => fmtMin(v) },
    { index: 4, label: "p90", align: "right", format: (v) => fmtMin(v) },
    { index: 5, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
  on_time: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "定時率", align: "right", bar: "pct", format: (v) => fmtPct(v) },
    { index: 3, label: "平均", align: "right", format: (v) => fmtMin(v) },
    { index: 4, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
  worst_5min: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "5分超回数", align: "right", bar: "raw", format: (v) => fmtNum(v) },
    { index: 3, label: "平均", align: "right", format: (v) => fmtMin(v) },
    { index: 4, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
  compare_ranking: [
    ROUTE_COL,
    { index: 1, label: "平日", align: "right", format: (v) => fmtMin(v) },
    { index: 2, label: "土日祝", align: "right", format: (v) => fmtMin(v) },
    { index: 3, label: "差", align: "right", bar: "delay", format: (v) => fmtMin(v) },
    {
      index: 4,
      label: "向き",
      align: "left",
      format: (v) => {
        const n = Number(v);
        if (!isFinite(n) || n === 0) return "—";
        return n > 0 ? "土日祝>平日" : "平日>土日祝";
      },
    },
  ],
  dow_weekend: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "曜日", align: "left" },
    { index: 3, label: "平均", align: "right", bar: "delay", format: (v) => fmtMin(v) },
    { index: 4, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
  dow_weekday: [
    ROUTE_COL,
    { index: 1, label: "種別", align: "left" },
    { index: 2, label: "曜日", align: "left" },
    { index: 3, label: "平均", align: "right", bar: "delay", format: (v) => fmtMin(v) },
    { index: 4, label: "観測数", align: "right", format: (v) => fmtNum(v) },
  ],
};

function fmtMin(v: unknown): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return `${n.toFixed(1)}分`;
}

function fmtPct(v: unknown): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function fmtNum(v: unknown): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return n.toLocaleString();
}

type Props = {
  reportType: string;
  rows: unknown[][];
};

export function ReportTable({ reportType, rows }: Props) {
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { format: formatRoute } = useRouteNames(id);
  const schema = SCHEMAS[reportType];

  const maxes = useMemo(() => {
    if (!schema) return new Map<number, number>();
    const m = new Map<number, number>();
    for (const col of schema) {
      if (!col.bar) continue;
      let mx = 0;
      for (const row of rows) {
        const v = Number(row[col.index]);
        if (isFinite(v) && Math.abs(v) > mx) mx = Math.abs(v);
      }
      m.set(col.index, mx || 1);
    }
    return m;
  }, [rows, schema]);

  if (!schema) {
    // Unknown type — fall back to raw key/value table
    return null;
  }

  return (
    <div style={{ width: "100%", overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr style={{ background: "var(--bg-soft)" }}>
            <th style={th(40)}>#</th>
            {schema.map((c) => (
              <th key={c.label} style={{ ...th(), textAlign: c.align ?? "left" }}>
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderTop: "1px solid var(--border-soft)" }}>
              <td style={{ ...td(), color: "var(--text-tertiary)", textAlign: "right" }}>{i + 1}</td>
              {schema.map((c) => {
                if (c === ROUTE_COL) {
                  const code = String(row[c.index] ?? "");
                  return (
                    <td key={c.label} style={{ ...td(), fontWeight: 500 }}>
                      {formatRoute(code)}
                    </td>
                  );
                }
                const raw = row[c.index];
                const text = c.format ? c.format(raw) : String(raw ?? "—");
                if (c.bar) {
                  const max = maxes.get(c.index) ?? 1;
                  const v = Number(raw);
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  const color = c.bar === "delay" ? delayColor(v) : "var(--accent)";
                  return (
                    <td key={c.label} style={{ ...td(), textAlign: c.align ?? "right", minWidth: 110 }}>
                      <BarCell text={text} ratio={ratio} color={color} />
                    </td>
                  );
                }
                return (
                  <td key={c.label} style={{ ...td(), textAlign: c.align ?? "left" }}>
                    {text}
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

function BarCell({ text, ratio, color }: { text: string; ratio: number; color: string }) {
  return (
    <div style={{ position: "relative", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
      <div
        style={{
          position: "absolute",
          right: 0,
          top: "50%",
          transform: "translateY(-50%)",
          height: 6,
          width: `${ratio * 100}%`,
          background: color,
          opacity: 0.18,
          borderRadius: 3,
          pointerEvents: "none",
        }}
      />
      <span style={{ position: "relative", color }}>{text}</span>
    </div>
  );
}

const th = (w?: number): React.CSSProperties => ({
  padding: "8px 10px",
  textAlign: "left",
  fontWeight: 500,
  color: "var(--text-secondary)",
  fontSize: 12,
  width: w,
});
const td = (): React.CSSProperties => ({
  padding: "6px 10px",
  fontSize: 13,
});
