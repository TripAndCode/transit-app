import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "react-router-dom";
import type { TFunction } from "i18next";
import { DELAY_THRESHOLDS, delayColor } from "../styles/tokens";
import { useRouteNames } from "../api/useRouteNames";
import { RouteLabel } from "./RouteLabel";
import { PendingNavLink } from "./navPending";
import { usePendingNavTarget } from "./navPendingContext";
import { RouteTitleTransition } from "./RouteTitleTransition";
import { isPlainLeftClick } from "../utils/clicks";
import { prefersReducedMotion } from "../utils/motion";
import { ServiceName } from "./ServiceName";
import { serviceValueLabel } from "../utils/filterValueLabels";
import { routeHref } from "../routes/destinations";
import { useAgencyId } from "../api/useAgencyId";
import { SHARED_TABLE, th, td } from "./tableStyles";
import { useCappedList } from "../hooks/useCappedList";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { Z_INDEX } from "../styles/zIndex";
import { formatNumber, fmtPct, formatDuration } from "../utils/format";
import "./ReportTable.css";
import { ServiceNote } from "./ServiceNote";

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
  /** The cell holds a service (an agency's calendar name, or a weekday or
   *  weekend group), shown in the UI's language where it has copy. */
  service?: true;
  /** The cell holds a route_code, shown as the route's label. */
  route?: true;
  /** A non-empty cell is a caveat, shown as a muted badge. */
  badge?: true;
  /** The cell is the row's observation count; under the report's floor it
   *  carries the few-data badge. */
  samples?: true;
};

// The route column of every per-route report. Kept in view while a wide table
// scrolls sideways, which only works for a first column.
const ROUTE_COL: Schema = { index: 0, labelKey: "common.route", align: "left", route: true };

// ranking + ranking_best share columns; only the API sort order differs.
const RANKING_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", service: true },
  { index: 2, labelKey: "reports.col.avg", align: "right", bar: "delay", unit: "min", format: fmtMinutes },
  { index: 3, labelKey: "reports.col.median", align: "right", unit: "min", format: fmtMinutes },
  { index: 4, labelKey: "reports.col.p90", align: "right", unit: "min", format: fmtMinutes },
  { index: 5, labelKey: "reports.col.samples", align: "right", samples: true, format: (v, t) => fmtNum(v, t) },
];

// dow_weekend + dow_weekday share columns; the API splits the rows by DOW
// group. The group itself (row index 2) is the report's own title, so it is
// not repeated as a column.
const DOW_COLS: Schema[] = [
  ROUTE_COL,
  { index: 1, labelKey: "reports.col.service", align: "left", service: true },
  { index: 3, labelKey: "reports.col.avg", align: "right", bar: "delay", unit: "min", format: fmtMinutes },
  { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

const SCHEMAS: Record<string, Schema[]> = {
  ranking: RANKING_COLS,
  ranking_best: RANKING_COLS,
  on_time: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", service: true },
    { index: 2, labelKey: "reports.col.on_time_pct", align: "right", bar: "pct", format: (v, t) => fmtPct(v, t) },
    { index: 3, labelKey: "reports.col.avg", align: "right", unit: "min", format: fmtMinutes },
    { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
    // 95% Wilson interval too wide to trust the percentage (see
    // pipeline/stats.py) — a caveat marker, not a plain value, so it's
    // blank rather than "false" for the common (confident) case.
    { index: 5, labelKey: "reports.col.confidence", align: "left", badge: true, format: (v, t) => fmtConfidence(v, t) },
  ],
  worst_5min: [
    ROUTE_COL,
    { index: 1, labelKey: "reports.col.service", align: "left", service: true },
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
  // (agency_name, route_code, service_type, date, scheduled_time,
  // actual_time, dep_delay_sec) -- one row per over-threshold departure
  // observation (see pipeline.reports.council.compute_delay_certificate).
  delay_certificate: [
    { index: 0, labelKey: "reports.col.agency_name", align: "left" },
    { index: 1, labelKey: "common.route", align: "left", route: true },
    { index: 2, labelKey: "reports.col.service", align: "left", service: true },
    { index: 3, labelKey: "reports.col.date", align: "left" },
    { index: 4, labelKey: "reports.col.scheduled_time", align: "left" },
    { index: 5, labelKey: "reports.col.actual_time", align: "left" },
    { index: 6, labelKey: "reports.col.delay", align: "right", format: (v) => formatDuration(v == null ? null : Number(v)) },
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
  /** The observation count below which a row is badged as thinly observed. */
  minSamples?: number | null;
};

export function ReportTable({ reportType, rows, minSamples }: Props) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const names = useRouteNames(id);
  const { search } = useLocation();
  const schema = SCHEMAS[reportType];
  // The visible row last clicked to travel; by row, not route, since a route
  // listed once per service would otherwise name two.
  const [travelling, setTravelling] = useState<number | null>(null);

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

  const serviceNote = schema.some((c) => c.service) && <ServiceNote />;
  const caveatOf = (c: Schema, raw: unknown) =>
    c.samples && minSamples != null && Number(raw) < minSamples ? t("reports.confidence_low_mark") : undefined;
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
                    <RouteCell agencyId={id} code={String(row[titleCol.index] ?? "")} names={names} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                  </span>
                )}
                {headline && (
                  <span className="report-cards__headline">
                    <span className="report-cards__label">{t(headline.labelKey)}</span>{" "}
                    {headline.bar === "delay" && isSevere(row[headline.index]) && <DelayMarker />}
                    <span className="num">{cardValue(headline, row[headline.index], t)}</span>
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
                        {!c.service && (
                          <>
                            <span className="report-cards__label">{t(c.labelKey)}</span>{" "}
                          </>
                        )}
                        <CellValue column={c} raw={row[c.index]} text={text} caveat={caveatOf(c, row[c.index])} />
                      </span>
                    );
                  })}
              </p>
            </li>
          ))}
        </ol>
        {showMore}
        {serviceNote}
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
                      <RouteCell agencyId={id} code={String(row[c.index] ?? "")} names={names} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                    </td>
                  );
                }
                const raw = row[c.index];
                const text = cellText(c, raw, t);
                if (c.bar) {
                  const max = maxes.get(c.index) ?? 1;
                  const v = Number(raw);
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  // The bar carries the ramp colour; the figure stays in the text
                  // colour, with an amber marker only past the severe threshold,
                  // so colour flags the few that need attention instead of all.
                  const isDelay = c.bar === "delay";
                  return (
                    <td key={c.labelKey} style={td({ align: c.align ?? "right" })}>
                      <BarCell
                        text={text}
                        ratio={ratio}
                        color={isDelay ? delayColor(v) : "var(--accent)"}
                        textColor={isDelay ? undefined : "var(--accent)"}
                        marked={isDelay && isSevere(raw)}
                      />
                    </td>
                  );
                }
                return (
                  <td key={c.labelKey} style={td({ align: c.align ?? "left" })}>
                    <CellValue column={c} raw={raw} text={text} caveat={caveatOf(c, raw)} />
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      {showMore}
      {serviceNote}
    </div>
  );
}

/** A row's route, as a link to its page: rows are where a route is found,
 *  so they are how it is opened. The scope carries over; routeHref drops what
 *  only chose this screen's report.
 *
 *  It opens as a screen navigation, and a plain click also lends the row's
 *  label the dossier title's transition name so the label travels into the
 *  title. The name lasts only while that navigation is pending, so one that
 *  is superseded leaves no stale name for a later transition to pair with.
 *  Under reduced motion nothing travels; a modified click stays the
 *  browser's. */
function RouteCell({
  agencyId,
  code,
  names,
  search,
  travels,
  onTravel,
}: {
  agencyId: number | null;
  code: string;
  names: ReturnType<typeof useRouteNames>;
  search: string;
  travels: boolean;
  onTravel: () => void;
}) {
  const pendingTo = usePendingNavTarget();
  if (agencyId == null || !code) return <RouteLabel code={code} names={names} />;
  const href = routeHref(agencyId, code, search);
  const travelling = travels && pendingTo === href;
  const link = (
    <PendingNavLink
      className={travelling ? "report-route-link report-route-link--travelling" : "report-route-link"}
      to={href}
      spinner={false}
      onClick={(e) => {
        if (isPlainLeftClick(e) && !prefersReducedMotion()) onTravel();
      }}
    >
      <RouteLabel code={code} names={names} />
      <span className="report-route-link__chevron" aria-hidden="true">
        {"\u00a0›"}
      </span>
    </PendingNavLink>
  );
  return travelling ? <RouteTitleTransition>{link}</RouteTitleTransition> : link;
}

function cellText(c: Schema, raw: unknown, t: TFunction): string {
  if (c.format) return c.format(raw, t);
  if (c.service && raw != null) return serviceValueLabel(String(raw), t);
  return String(raw ?? "—");
}

/** A card has no column header to carry the unit, so the value does. */
function cardValue(c: Schema, raw: unknown, t: TFunction): string {
  const text = cellText(c, raw, t);
  return c.unit && raw != null ? t("reports.card.value_with_unit", { value: text, unit: t("common.unit_min") }) : text;
}

/** A plain cell: a service in the UI's language where it has copy, a caveat
 *  as a muted badge, anything else as its formatted text, followed by the
 *  row's own caveat when it has one. */
function CellValue({ column, raw, text, caveat }: { column: Schema; raw: unknown; text: string; caveat?: string }) {
  if (column.service && raw != null) return <ServiceName value={String(raw)} />;
  if (column.badge && text) return <span className="caveat-badge">{text}</span>;
  if (caveat) {
    return (
      <>
        {text} <span className="caveat-badge">{caveat}</span>
      </>
    );
  }
  return <>{text}</>;
}

/** At or past the colour ramp's severe threshold. */
function isSevere(raw: unknown): boolean {
  const v = Number(raw);
  return Number.isFinite(v) && v >= DELAY_THRESHOLDS.severe;
}

function DelayMarker() {
  return <span data-testid="delay-marker" className="delay-marker" aria-hidden="true" />;
}

function BarCell({
  text,
  ratio,
  color,
  textColor,
  marked,
}: {
  text: string;
  ratio: number;
  color: string;
  textColor?: string;
  marked: boolean;
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
      {marked && <DelayMarker />}
      <span className="num" style={textColor ? { color: textColor } : undefined}>{text}</span>
    </div>
  );
}
