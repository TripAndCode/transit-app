import { useState, type ReactNode } from "react";
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
import { routeHref, type RouteTableSort } from "../routes/destinations";
import { useAgencyId } from "../api/useAgencyId";
import { SHARED_TABLE, th, td } from "./tableStyles";
import { useCappedList } from "../hooks/useCappedList";
import { useMediaQuery, MOBILE_BREAKPOINT_QUERY } from "../hooks/useMediaQuery";
import { Z_INDEX } from "../styles/zIndex";
import { fixedTenth, fmtPct, formatDuration, formatNumber, roundTenth } from "../utils/format";
import "./ReportTable.css";
import { ServiceNote } from "./ServiceNote";
import { Tooltip } from "./Tooltip";

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
  /** When set, draws an inline bar; a delay bar takes the ramp colour of the
   *  minutes the cell prints. */
  bar?: "delay" | "pct" | "raw";
  /** A delay bar's figure carries an amber marker at or past the severe
   *  threshold. Left off where most rows pass it, since a mark on every row
   *  flags nothing. */
  severeMarker?: true;
  /** The Routes table sort this column's figure is. The sorted column is the
   *  one that carries its bar, its weight and, on a phone, each card's
   *  headline. */
  sort?: RouteTableSort;
  /** A minutes column: the header names the unit once, and each cell holds
   *  the bare figure. */
  unit?: "min";
  format?: (v: unknown, t: TFunction) => string;
  /** The cell holds a service (an agency's calendar name), shown in the
   *  UI's language where it has copy. */
  service?: true;
  /** The cell holds a route_code, shown as the route's label: `compact` on
   *  one line with the plate and line name, where it runs in the tooltip. */
  route?: "full" | "compact";
  /** The row's caveat on this cell's figure, shown as a muted badge after it,
   *  with what it measures when the badge is hovered or focused. */
  caveat?: (row: unknown[], t: TFunction) => { mark: string; tip?: string } | undefined;
  /** The cell is the row's observation count; under the report's floor it
   *  carries the few-data badge. */
  samples?: true;
};

// The route column of every per-route report. Kept in view while a wide table
// scrolls sideways, which only works for a first column.
const ROUTE_COL: Schema = { index: 0, labelKey: "common.route", align: "left", route: "full" };

// The day-type reports leave out row index 1, their service slot: a row is
// one route over all its observations, and the slot only repeats the active
// service filter, which the scope already names. The Routes table's rows
// carry no such slot.

// dow_weekend + dow_weekday share columns; the API splits the rows by the
// agency's day type. The day type itself (row index 2) is the report's own
// title, so it is not repeated as a column.
const DOW_COLS: Schema[] = [
  ROUTE_COL,
  { index: 3, labelKey: "reports.col.avg", align: "right", bar: "delay", severeMarker: true, unit: "min", format: fmtMinutes },
  { index: 4, labelKey: "reports.col.samples", align: "right", format: (v, t) => fmtNum(v, t) },
];

const SCHEMAS: Record<string, Schema[]> = {
  // (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples,
  // low_confidence, margin_pp): every figure of a route, sorted by one.
  route_table: [
    { ...ROUTE_COL, route: "compact" },
    { index: 1, labelKey: "reports.col.avg", align: "right", bar: "delay", unit: "min", format: fmtMinutes, sort: "avg" },
    {
      index: 2,
      labelKey: "reports.col.on_time_pct",
      align: "right",
      bar: "pct",
      format: (v, t) => fmtPct(v, t),
      sort: "on_time",
      // The 95% Wilson interval is too wide to trust the rate (see
      // pipeline/stats.py). It narrows toward 0% and 100%, so it says
      // nothing about the observation count on its own.
      caveat: (row, t) =>
        row[6] === true
          ? { mark: t("reports.wide_margin_mark"), tip: t("reports.wide_margin_tip", { margin: fixedTenth(Number(row[7])) }) }
          : undefined,
    },
    { index: 3, labelKey: "reports.col.over_5min_share", align: "right", bar: "pct", format: (v, t) => fmtPct(v, t), sort: "late5_share" },
    { index: 4, labelKey: "reports.col.over_5min_count", align: "right", bar: "raw", format: (v, t) => fmtNum(v, t), sort: "late5_count" },
    { index: 5, labelKey: "reports.col.samples", align: "right", samples: true, format: (v, t) => fmtNum(v, t) },
  ],
  compare_ranking: [
    ROUTE_COL,
    { index: 1, labelKey: "common.service_value.平日", align: "right", unit: "min", format: fmtMinutes }, // i18n-ignore: query contract
    { index: 2, labelKey: "common.service_value.土日祝", align: "right", unit: "min", format: fmtMinutes }, // i18n-ignore: query contract
    { index: 3, labelKey: "reports.col.diff", align: "right", bar: "delay", severeMarker: true, unit: "min", format: fmtMinutes },
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
  // actual_time, dep_delay_sec, stop_name, stop_is_first) -- one row per
  // over-threshold departure observation (see
  // pipeline.reports.council.compute_delay_certificate). The stop is marked
  // as the trip's start only where the timetable starts the trip there.
  delay_certificate: [
    { index: 0, labelKey: "reports.col.agency_name", align: "left" },
    { index: 1, labelKey: "common.route", align: "left", route: "full" },
    { index: 2, labelKey: "reports.col.service", align: "left", service: true },
    { index: 3, labelKey: "reports.col.date", align: "left" },
    { index: 4, labelKey: "reports.col.scheduled_time", align: "left" },
    { index: 5, labelKey: "reports.col.actual_time", align: "left" },
    { index: 6, labelKey: "reports.col.delay", align: "right", format: (v) => formatDuration(v == null ? null : Number(v)) },
    {
      index: 7,
      labelKey: "reports.col.recorded_stop",
      align: "left",
      caveat: (row, t) => (row[8] === true ? { mark: t("reports.col.trip_start") } : undefined),
    },
  ],
};

function fmtMinutes(v: unknown): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return fixedTenth(n);
}

function fmtNum(v: unknown, _t: TFunction): string {
  if (v == null) return "—";
  const n = Number(v);
  if (!isFinite(n)) return "—";
  return formatNumber(n);
}

// Module-scope pure function rather than an in-render IIFE — see
// eslint.config.js's manual-memoization ban comment for why this shape is
// preferred over an inline immediately-invoked function expression.
function computeColumnMaxes(schema: Schema[] | undefined, rows: unknown[][]): Map<number, number> {
  if (!schema) return new Map<number, number>();
  const m = new Map<number, number>();
  for (const col of schema) {
    if (!col.bar) continue;
    // A percentage has a fixed whole; scaled to the column's best instead, a
    // list of low rates would draw its top row as a full bar.
    if (col.bar === "pct") {
      m.set(col.index, 100);
      continue;
    }
    let mx = 0;
    for (const row of rows) {
      const v = Number(row[col.index]);
      if (isFinite(v) && Math.abs(v) > mx) mx = Math.abs(v);
    }
    m.set(col.index, mx || 1);
  }
  return m;
}

// The share of the table a compact route column takes: the room where a
// route runs has before its ellipsis, beside figures that never wrap.
const COMPACT_ROUTE_SHARE = "45%";

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
  /** The Routes table's sort: its column carries the bar and the weight, and
   *  heads each card on a phone. */
  sortBy?: RouteTableSort;
  /** Which way the sorted column runs, for assistive technology. */
  sortDirection?: "ascending" | "descending";
};

type Caveat = { mark: string; tip?: string };

export function ReportTable({ reportType, rows, minSamples, sortBy, sortDirection }: Props) {
  const { t } = useTranslation();
  const id = useAgencyId();
  const names = useRouteNames(id);
  const { search } = useLocation();
  const schema = SCHEMAS[reportType];
  // The visible row last clicked to travel; by row, not route, since a route
  // can fill several rows (a delay certificate lists each departure).
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

  const basis = sortBy == null ? undefined : schema.find((c) => c.sort === sortBy);
  const barred = (c: Schema) => c.bar != null && (basis == null || c === basis);
  const serviceNote = schema.some((c) => c.service) && <ServiceNote />;
  const caveatsOf = (c: Schema, row: unknown[]): Caveat[] => {
    const own = c.caveat?.(row, t);
    const thin = c.samples && minSamples != null && Number(row[c.index]) < minSamples;
    return [...(own ? [own] : []), ...(thin ? [{ mark: t("reports.confidence_low_mark") }] : [])];
  };
  const showMore = cappedRows.remaining > 0 && (
    <button type="button" className="btn-ghost" onClick={cappedRows.showMore}>
      {t("common.show_more", { count: cappedRows.remaining })}
    </button>
  );

  if (compact) {
    // Seven columns can't share a phone's width without characters stacking,
    // so each row becomes one item: rank, route and the sorted (else the
    // barred) figure on top, every other column on a wrapping line beneath.
    // A report without a route column has no title.
    const titleCol = schema.find((c) => c.route);
    const headline = basis ?? schema.find((c) => c.bar);
    return (
      <div>
        <ol className="report-cards">
          {cappedRows.visible.map((row, i) => (
            <li key={i} className="report-cards__item">
              <div className="report-cards__head">
                <span className="report-cards__rank">{i + 1}</span>
                {titleCol && (
                  <span className="report-cards__route">
                    <RouteCell agencyId={id} code={String(row[titleCol.index] ?? "")} names={names} compact={titleCol.route === "compact"} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                  </span>
                )}
                {headline && (
                  <span className="report-cards__headline">
                    <span className="report-cards__label">{t(headline.labelKey)}</span>{" "}
                    {headline.severeMarker && isSevere(row[headline.index]) && <DelayMarker />}
                    <CellValue column={headline} raw={row[headline.index]} text={cardValue(headline, row[headline.index], t)} caveats={caveatsOf(headline, row)} />
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
                        <CellValue column={c} raw={row[c.index]} text={text} caveats={caveatsOf(c, row)} />
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
            <th style={{ ...th({ width: 40 }), whiteSpace: "nowrap" }}>#</th>
            {schema.map((c) => (
              <th
                key={c.labelKey}
                className={c === basis ? "report-col--basis" : undefined}
                aria-sort={c === basis ? sortDirection : undefined}
                // A header names its column on one line; a wrapped one
                // stands taller than every row under it.
                style={{
                  ...th(),
                  textAlign: c.align ?? "left",
                  whiteSpace: "nowrap",
                  ...(c === basis ? { color: "var(--text-primary)" } : null),
                  ...(c.route === "compact" ? { width: COMPACT_ROUTE_SHARE } : null),
                  ...(c === schema[0] && c.route ? STICKY_HEAD : null),
                }}
              >
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
                      className={c.route === "compact" ? "report-route-cell--compact" : undefined}
                      // A full label names the line and where it goes; below
                      // this width it breaks onto a third line. A compact
                      // label holds one line.
                      style={{
                        ...td(),
                        ...(c === schema[0] ? STICKY_CELL : null),
                        ...(c.route === "compact" ? { minWidth: "15em", width: COMPACT_ROUTE_SHARE } : { minWidth: "16em" }),
                        fontWeight: 500,
                        wordBreak: "keep-all",
                      }}
                    >
                      <RouteCell agencyId={id} code={String(row[c.index] ?? "")} names={names} compact={c.route === "compact"} search={search} travels={travelling === i} onTravel={() => setTravelling(i)} />
                    </td>
                  );
                }
                const raw = row[c.index];
                const value = <CellValue column={c} raw={raw} text={cellText(c, raw, t)} caveats={caveatsOf(c, row)} />;
                const className = c === basis ? "report-cell--basis" : undefined;
                if (barred(c)) {
                  // A delay bar is drawn from the minutes the cell prints, so
                  // two rows printing the same figure share a colour.
                  const v = c.bar === "delay" ? shownMinutes(raw) : Number(raw);
                  const max = maxes.get(c.index) ?? 1;
                  const ratio = isFinite(v) ? Math.min(1, Math.abs(v) / max) : 0;
                  return (
                    <td key={c.labelKey} className={className} style={td({ align: c.align ?? "right" })}>
                      <BarCell
                        ratio={ratio}
                        color={c.bar === "delay" ? delayColor(v) : "var(--accent)"}
                        marked={!!c.severeMarker && isSevere(raw)}
                      >
                        {value}
                      </BarCell>
                    </td>
                  );
                }
                return (
                  <td key={c.labelKey} className={className} style={td({ align: c.align ?? "left" })}>
                    {value}
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
  compact,
  search,
  travels,
  onTravel,
}: {
  agencyId: number | null;
  code: string;
  names: ReturnType<typeof useRouteNames>;
  compact: boolean;
  search: string;
  travels: boolean;
  onTravel: () => void;
}) {
  const pendingTo = usePendingNavTarget();
  if (agencyId == null || !code) return <RouteLabel code={code} names={names} compact={compact} />;
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
      <RouteLabel code={code} names={names} compact={compact} />
      <span className="report-route-link__chevron" aria-hidden="true">
        {" ›"}
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

/** A plain cell: a service in the UI's language where it has copy, anything
 *  else as its formatted text, followed by the row's caveats on it as muted
 *  badges. A badge with a tip takes keyboard focus, so the tip is reachable
 *  without a pointer. */
function CellValue({
  column,
  raw,
  text,
  caveats,
}: {
  column: Schema;
  raw: unknown;
  text: string;
  caveats: Caveat[];
}) {
  if (column.service && raw != null) return <ServiceName value={String(raw)} />;
  return (
    <>
      {text}
      {caveats.map(({ mark, tip }) => (
        <span key={mark}>
          {" "}
          {tip ? (
            <Tooltip label={tip}>
              {/* eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- plain badge, not a control; keyboard-focusable only so the margin it stands for is reachable */}
              <span className="caveat-badge" tabIndex={0}>
                {mark}
              </span>
            </Tooltip>
          ) : (
            <span className="caveat-badge">{mark}</span>
          )}
        </span>
      ))}
    </>
  );
}

/** The minutes a delay cell prints: its colour and bar are drawn from the
 *  same rounded figure as its text, never from the unrounded one. */
function shownMinutes(raw: unknown): number {
  return roundTenth(Number(raw));
}

/** At or past the colour ramp's severe threshold, as printed. */
function isSevere(raw: unknown): boolean {
  const v = shownMinutes(raw);
  return Number.isFinite(v) && v >= DELAY_THRESHOLDS.severe;
}

function DelayMarker() {
  return <span data-testid="delay-marker" className="delay-marker" aria-hidden="true" />;
}

function BarCell({
  ratio,
  color,
  marked,
  children,
}: {
  ratio: number;
  color: string;
  marked: boolean;
  children: ReactNode;
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
      <span>{children}</span>
    </div>
  );
}
