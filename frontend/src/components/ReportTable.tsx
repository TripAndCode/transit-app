import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import type { TFunction } from "i18next";
import { delayColor, delayTextColor } from "../styles/tokens";
import { useRouteNames } from "../api/useRouteNames";
import { RouteLabel } from "./RouteLabel";
import { routeHref } from "../routes/destinations";
import { useAgencyId } from "../api/useAgencyId";
import { SHARED_TABLE, th, td } from "./tableStyles";
import { useCappedList } from "../hooks/useCappedList";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { Z_INDEX } from "../styles/zIndex";
import { formatNumber, fmtPct } from "../utils/format";
import "./ReportTable.css";

const ROWS_CAP = 200;
// A phone list item is several times a table row's height, so the first
// screenful is shorter.
const PHONE_ROWS_CAP = 25;

type Schema = {
  /** Column index in the row tuple */
  index: number;
  /** i18n key for the column header label */
  labelKey: string;
  align?: "left" | "right";
  /** When set, draws an inline bar; ``barColor`` is in delay-min space. */
  bar?: "delay" | "pct" | "raw";
  /** A minutes column: the header names the unit once, and each cell holds
   *  the bare figure. */
  unit?: "min";
  format?: (v: unknown, t: TFunction) => string;
  /**
   * When set, the raw cell value is treated as a translation-key suffix:
   * the rendered text is `t(\`${valueKey}.${raw}\`, { defaultValue: raw })`.
   * Use this for columns whose DB values are wire contracts (e.g. service_type
   * "平日" / "土日祝") but should display in the active locale. // i18n-ignore: JSDoc
   */
  valueKey?: string;
  /** The cell holds a route_code, shown as the route's label. */
  route?: true;
};

// The route column of every per-route report. Kept in view while a wide table
// scrolls sideways, which only works for a first column.
const ROUTE_COL: Schema = { index: 0, labelKey: "common.route", align: "left", route: true };

// ranking + ranking_best share columns; only the API sort order differs.
const RANKING_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
  { index: 2, labelKey: "reports.col.avg", align: "right", bar: "delay", unit: "min", format: fmtMinutes },
  { index: 3, labelKey: "reports.col.median", align: "right", unit: "min", format: fmtMinutes },
  { index: 4, labelKey: "reports.col.p90", align: "right", unit: "min", format: fmtMinutes },
  { index: 5, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

// dow_weekend + dow_weekday share columns; the API splits the rows by DOW group.
const DOW_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
  { index: 2, labelKey: "reports.col.dow", align: "left" },
  { index: 3, labelKey: "reports.col.avg", align: "right", bar: "delay", unit: "min", format: fmtMinutes },
  { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

const SCHEMAS: Record<string, Schema[]> = {
  ranking: RANKING_COLS,
  ranking_best: RANKING_COLS,
  on_time: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
    { index: 2, labelKey: "reports.col.on_time_pct", align: "right", bar: "pct", format: (v, t) => fmtPct(v, t) },
    { index: 3, labelKey: "reports.col.avg", align: "right", unit: "min", format: fmtMinutes },
    { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
    // 95% Wilson interval too wide to trust the percentage (see
    // pipeline/stats.py) — a caveat marker, not a plain value, so it's
    // blank rather than "false" for the common (confident) case.
    { index: 5, labelKey: "reports.col.confidence", align: "left", format: (v, t) => fmtConfidence(v, t) },
  ],
  worst_5min: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
    { index: 2, labelKey: "reports.col.over_5min_count", align: "right", bar: "raw", format: (v, t) => fmtNum(v, t) },
    { index: 3, labelKey: "reports.col.avg", align: "right", unit: "min", format: fmtMinutes },
    { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
  ],
  compare_ranking: [
    ROUTE_COL,
    { index: 1, labelKey: "common.service_value.平日", align: "right", unit: "min", format: fmtMinutes }, // i18n-ignore: query contract
    { index: 2, labelKey: "common.service_value.土日祝", align: "right", unit: "min", format: fmtMinutes }, // i18n-ignore: query contract
    { index: 3, labelKey: "reports.col.diff", align: "right", bar: "delay", unit: "min", format: fmtMinutes },
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
  // (on_time_pct, avg_delay_min, samples, planned_trips, executed_trips,
  // service_delivered_pct) -- a single pooled whole-agency row, not a
  // per-route ranking (see pipeline.reports.council.compute_council_summary).
  council_summary: [
    { index: 0, labelKey: "reports.col.on_time_pct", align: "right", format: (v, t) => fmtPct(v, t) },
    { index: 1, labelKey: "reports.col.avg", align: "right", unit: "min", format: fmtMinutes },
    { index: 2, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
    { index: 3, labelKey: "reports.col.planned_trips", align: "right", format: (v, t) => fmtNum(v, t) },
    { index: 4, labelKey: "reports.col.executed_trips", align: "right", format: (v, t) => fmtNum(v, t) },
    { index: 5, labelKey: "reports.col.service_delivered_pct", align: "right", format: (v, t) => fmtPct(v, t) },
  ],
  // (agency_name, route_code, service_type, date, scheduled_time,
  // actual_time, dep_delay_sec) -- one row per over-threshold departure
  // observation (see pipeline.reports.council.compute_delay_certificate).
  delay_certificate: [
    { index: 0, labelKey: "reports.col.agency_name", align: "left" },
    { index: 1, labelKey: "common.route", align: "left", route: true },
    { index: 2, labelKey: "reports.col.service", align: "left", valueKey: "common.service_value" },
    { index: 3, labelKey: "reports.col.date", align: "left" },
    { index: 4, labelKey: "reports.col.scheduled_time", align: "left" },
    { index: 5, labelKey: "reports.col.actual_time", align: "left" },
    { index: 6, labelKey: "reports.col.delay_sec", align: "right", format: (v, t) => fmtNum(v, t) },
  ],
};

function fmtMinutes(v: unknown): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return n.toFixed(1);
}

function fmtNum(v: unknown, _t: TFunction): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return formatNumber(n);
}

function fmtConfidence(v: unknown, t: TFunction): string {
  return v === true ? t("reports.confidence_low_mark") : "";
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

// The route column stays in view while a table wider than its panel
// scrolls sideways. It needs an opaque background for rows to pass under:
// the page's, since the table sits directly on it.
const STICKY_CELL = { position: "sticky", left: 0, zIndex: Z_INDEX.raised, background: "var(--bg-page)" } as const;
const STICKY_HEAD = { position: "sticky", left: 0, zIndex: Z_INDEX.raised, background: "var(--bg-soft)" } as const;

type Props = {
  reportType: string;
  rows: unknown[][];
};

export function ReportTable({ reportType, rows }: Props) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const names = useRouteNames(id);
  const { search } = useLocation();
  const schema = SCHEMAS[reportType];

  const compact = useMediaQuery(MOBILE_BREAKPOINT_QUERY);
  const cappedRows = useCappedList(
    rows,
    compact ? PHONE_ROWS_CAP : ROWS_CAP,
    `${reportType}:${compact ? "phone" : "wide"}`,
  );

  if (!schema) {
    // Unknown type: render nothing.
    return null;
  }

  const showMore = cappedRows.remaining > 0 && (
    <button type="button" className="btn-ghost" onClick={cappedRows.showMore}>
      {t("common.show_more", { count: cappedRows.remaining })}
    </button>
  );

  if (compact) {
    // Seven columns can't share a phone's width without characters stacking,
    // so each row becomes one item: rank, route and the barred figure on top,
    // every other column on a wrapping line beneath. A report without a
    // route column has no title.
    const titleCol = schema.find((c) => c.route);
    const headline = schema.find((c) => c.bar);
    return (
      <div>
        <ol className="report-cards">
          {cappedRows.visible.map((row, i) => (
            <li key={i} className="report-cards__item">
              <div className="report-cards__head">
                <span className="report-cards__rank">{i + 1}</span>
                {titleCol && (
                  <span className="report-cards__route">
                    <RouteCell agencyId={id} code={String(row[titleCol.index] ?? "")} names={names} search={search} />
                  </span>
                )}
                {headline && (
                  <span
                    className="report-cards__headline"
                    style={headline.bar === "delay" ? { color: delayTextColor(Number(row[headline.index])) } : undefined}
                  >
                    <span className="report-cards__label">{t(headline.labelKey)}</span>{" "}
                    <span>{cardValue(headline, row[headline.index], t)}</span>
                  </span>
                )}
              </div>
              <p className="report-cards__meta">
                {schema
                  .filter((c) => c !== titleCol && c !== headline)
                  .map((c) => {
                    // A blank table cell reads as nothing under its header;
                    // on a card the label would stand alone.
                    const text = cardValue(c, row[c.index], t);
                    if (text === "") return null;
                    return (
                      <span key={c.labelKey} className="report-cards__field">
                        {c.valueKey == null && (
                          <>
                            <span className="report-cards__label">{t(c.labelKey)}</span>{" "}
                          </>
                        )}
                        <span>{text}</span>
                      </span>
                    );
                  })}
              </p>
            </li>
          ))}
        </ol>
        {showMore}
      </div>
    );
  }

  const maxes = computeColumnMaxes(schema, rows);
  return (
    <div className="table-scroll" style={{ width: "100%", overflowX: "auto" }}>
      <table style={SHARED_TABLE}>
        <thead>
          <tr style={{ background: "var(--bg-soft)" }}>
            <th style={th({ width: 40 })}>#</th>
            {schema.map((c) => (
              <th key={c.labelKey} style={{ ...th(), textAlign: c.align ?? "left", ...(c === ROUTE_COL ? STICKY_HEAD : null) }}>
                {c.unit ? t("reports.col.with_unit", { label: t(c.labelKey), unit: t("common.unit_min") }) : t(c.labelKey)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {cappedRows.visible.map((row, i) => (
            <tr
              key={i}
              className={schema.some((c) => c.route) ? "report-row--link" : undefined}
              style={{ borderTop: "1px solid var(--border-soft)" }}
            >
              <td style={{ ...td({ align: "right" }), color: "var(--text-tertiary)" }}>{i + 1}</td>
              {schema.map((c) => {
                if (c.route) {
                  return (
                    <td
                      key={c.labelKey}
                      // A label names the line and where it goes; below this
                      // width it breaks onto a third line.
                      style={{ ...td(), ...(c === ROUTE_COL ? STICKY_CELL : null), minWidth: "16em", fontWeight: 500, wordBreak: "keep-all" }}
                    >
                      <RouteCell agencyId={id} code={String(row[c.index] ?? "")} names={names} search={search} />
                    </td>
                  );
                }
                const raw = row[c.index];
                const text = cellText(c, raw, t);
                if (c.bar) {
                  const max = maxes.get(c.index) ?? 1;
                  const v = Number(raw);
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  // The bar fill can stay the plain ramp colour (it's a mark, not
                  // text); the label sitting on top needs the text-safe variant,
                  // since delayColor()'s ok/mild/moderate fall short of AA as text.
                  const color = c.bar === "delay" ? delayColor(v) : "var(--accent)";
                  const textColor = c.bar === "delay" ? delayTextColor(v) : "var(--accent)";
                  return (
                    <td key={c.labelKey} style={td({ align: c.align ?? "right" })}>
                      <BarCell text={text} ratio={ratio} color={color} textColor={textColor} />
                    </td>
                  );
                }
                return (
                  <td key={c.labelKey} style={td({ align: c.align ?? "left" })}>
                    {text}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {showMore}
    </div>
  );
}

/** A row's route, as a link to its page: rows are where a route is found,
 *  so they are how it is opened. The scope carries over; routeHref drops what
 *  only chose this screen's report. */
function RouteCell({
  agencyId,
  code,
  names,
  search,
}: {
  agencyId: number | null;
  code: string;
  names: ReturnType<typeof useRouteNames>;
  search: string;
}) {
  if (agencyId == null || !code) return <RouteLabel code={code} names={names} />;
  return (
    <Link className="report-route-link" to={routeHref(agencyId, code, search)}>
      <RouteLabel code={code} names={names} />
      <span className="report-route-link__chevron" aria-hidden="true">
        {" ›"}
      </span>
    </Link>
  );
}

function cellText(c: Schema, raw: unknown, t: TFunction): string {
  if (c.format) return c.format(raw, t);
  if (c.valueKey != null && raw != null) {
    const rawStr = String(raw);
    return t(`${c.valueKey}.${rawStr}`, { defaultValue: rawStr });
  }
  return String(raw ?? "—");
}

/** A card has no column header to carry the unit, so the value does. */
function cardValue(c: Schema, raw: unknown, t: TFunction): string {
  const text = cellText(c, raw, t);
  return c.unit && raw != null ? t("reports.card.value_with_unit", { value: text, unit: t("common.unit_min") }) : text;
}

function BarCell({
  text,
  ratio,
  color,
  textColor,
}: {
  text: string;
  ratio: number;
  color: string;
  textColor: string;
}) {
  return (
    // The bar has its own track beside the figure: drawn under the text, it
    // read as a strike-through.
    <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 8 }}>
      <div
        data-testid="bar-track"
        aria-hidden="true"
        style={{ width: 40, height: 6, flex: "none", borderRadius: 3, background: "var(--surface-2)", overflow: "hidden" }}
      >
        <div style={{ width: `${ratio * 100}%`, height: "100%", borderRadius: 3, background: color, opacity: 0.6 }} />
      </div>
      <span style={{ color: textColor }}>{text}</span>
    </div>
  );
}
