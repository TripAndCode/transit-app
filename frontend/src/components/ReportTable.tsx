import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { delayColor } from "../styles/tokens";
import { useRouteNames } from "../api/useRouteNames";
import { useParams } from "react-router-dom";

type Schema = {
  /** Column index in the row tuple */
  index: number;
  /** i18n key for the column header label */
  labelKey: string;
  align?: "left" | "right";
  /** When set, draws an inline bar; ``barColor`` is in delay-min space. */
  bar?: "delay" | "pct" | "raw";
  format?: (v: unknown, t: TFunction) => string;
  /**
   * When set, the raw cell value is treated as a translation-key suffix:
   * the rendered text is `t(\`${valueKey}.${raw}\`, { defaultValue: raw })`.
   * Use this for columns whose DB values are wire contracts (e.g. service_type
   * "平日" / "土日祝") but should display in the active locale. // i18n-ignore: JSDoc
   */
  valueKey?: string;
};

// Sentinel reference for the route column. The render path uses reference
// equality (`c === ROUTE_COL`) to swap in route-name formatting, so every
// schema below must reuse this exact instance.
const ROUTE_COL: Schema = { index: 0, labelKey: "reports.col.route", align: "left" };

// ranking + ranking_best share columns; only the API sort order differs.
const RANKING_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
  { index: 2, labelKey: "reports.col.avg", align: "right", bar: "delay", format: (v, t) => fmtMin(v, t) },
  { index: 3, labelKey: "reports.col.median", align: "right", format: (v, t) => fmtMin(v, t) },
  { index: 4, labelKey: "reports.col.p90", align: "right", format: (v, t) => fmtMin(v, t) },
  { index: 5, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

// dow_weekend + dow_weekday share columns; the API splits the rows by DOW group.
const DOW_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
  { index: 2, labelKey: "reports.col.dow", align: "left" },
  { index: 3, labelKey: "reports.col.avg", align: "right", bar: "delay", format: (v, t) => fmtMin(v, t) },
  { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

const SCHEMAS: Record<string, Schema[]> = {
  ranking: RANKING_COLS,
  ranking_best: RANKING_COLS,
  on_time: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
    { index: 2, labelKey: "reports.col.on_time_pct", align: "right", bar: "pct", format: (v, t) => fmtPct(v, t) },
    { index: 3, labelKey: "reports.col.avg", align: "right", format: (v, t) => fmtMin(v, t) },
    { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
  ],
  worst_5min: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
    { index: 2, labelKey: "reports.col.over_5min_count", align: "right", bar: "raw", format: (v, t) => fmtNum(v, t) },
    { index: 3, labelKey: "reports.col.avg", align: "right", format: (v, t) => fmtMin(v, t) },
    { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
  ],
  compare_ranking: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.weekday", align: "right", format: (v, t) => fmtMin(v, t) },
    { index: 2, labelKey: "reports.col.weekend", align: "right", format: (v, t) => fmtMin(v, t) },
    { index: 3, labelKey: "reports.col.diff", align: "right", bar: "delay", format: (v, t) => fmtMin(v, t) },
    {
      index: 4,
      labelKey: "reports.col.direction",
      align: "left",
      format: (v, t) => {
        const n = Number(v);
        if (!isFinite(n) || n === 0) return "—";
        return n > 0 ? t("reports.direction.weekend_higher") : t("reports.direction.weekday_higher");
      },
    },
  ],
  dow_weekend: DOW_COLS,
  dow_weekday: DOW_COLS,
};

function fmtMin(v: unknown, t: TFunction): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return `${n.toFixed(1)}${t("common.unit_min")}`;
}

function fmtPct(v: unknown, _t: TFunction): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function fmtNum(v: unknown, _t: TFunction): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return n.toLocaleString();
}

// Module-scope pure function rather than an in-render IIFE — see
// eslint.config.js's manual-memoization ban comment for why this shape is
// preferred over an inline immediately-invoked function expression.
function computeColumnMaxes(schema: Schema[] | undefined, rows: unknown[][]): Map<number, number> {
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
}

type Props = {
  reportType: string;
  rows: unknown[][];
};

export function ReportTable({ reportType, rows }: Props) {
  const { t } = useTranslation();
  const { agencyId } = useParams();
  const id = agencyId ? Number(agencyId) : null;
  const { format: formatRoute } = useRouteNames(id);
  const schema = SCHEMAS[reportType];

  const maxes = computeColumnMaxes(schema, rows);

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
              <th key={c.labelKey} style={{ ...th(), textAlign: c.align ?? "left" }}>
                {t(c.labelKey)}
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
                    <td key={c.labelKey} style={{ ...td(), fontWeight: 500 }}>
                      {formatRoute(code)}
                    </td>
                  );
                }
                const raw = row[c.index];
                let text: string;
                if (c.format) {
                  text = c.format(raw, t);
                } else if (c.valueKey != null && raw != null) {
                  const rawStr = String(raw);
                  text = t(`${c.valueKey}.${rawStr}`, { defaultValue: rawStr });
                } else {
                  text = String(raw ?? "—");
                }
                if (c.bar) {
                  const max = maxes.get(c.index) ?? 1;
                  const v = Number(raw);
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  const color = c.bar === "delay" ? delayColor(v) : "var(--accent)";
                  return (
                    <td key={c.labelKey} style={{ ...td(), textAlign: c.align ?? "right", minWidth: 110 }}>
                      <BarCell text={text} ratio={ratio} color={color} />
                    </td>
                  );
                }
                return (
                  <td key={c.labelKey} style={{ ...td(), textAlign: c.align ?? "left" }}>
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
