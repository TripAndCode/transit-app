import type { TFunction } from "i18next";
import type { RouteTableSort } from "../routes/destinations";
import { fixedTenth, fmtPct, formatDuration, formatNumber } from "../utils/format";

/** One column `ReportTable` draws. */
export type Schema = {
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
  /** The cell is the row's observation count. Its value under the report's
   *  floor decides the few-data badge, which this cell carries while it is
   *  shown and the figures beside it carry while it is hidden. */
  samples?: true;
  /** A statistic about the row's figures rather than a figure of its own:
   *  shown only while the reader asks for statistics. */
  stat?: true;
  /** The cell is an early-departure rate. Its header names the threshold
   *  the rate counts past, so the column shows only where the caller gives
   *  that threshold. */
  early?: true;
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
  { index: 4, labelKey: "reports.col.samples", align: "right", samples: true, stat: true, format: (v, t) => fmtNum(v, t) },
];

/** The columns of each report `ReportTable` draws, by report type. */
export const SCHEMAS: Record<string, Schema[]> = {
  // (route_code, avg_min, on_time_pct, late5_pct, late5_count, samples,
  // early_pct, low_confidence, margin_pp): every figure of a route, sorted by
  // one of them.
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
        row[7] === true
          ? { mark: t("reports.wide_margin_mark"), tip: t("reports.wide_margin_tip", { margin: fixedTenth(Number(row[8])) }) }
          : undefined,
    },
    { index: 6, labelKey: "reports.col.early_share", align: "right", early: true, format: (v, t) => fmtPct(v, t) },
    { index: 3, labelKey: "reports.col.over_5min_share", align: "right", bar: "pct", format: (v, t) => fmtPct(v, t), sort: "late5_share" },
    { index: 4, labelKey: "reports.col.over_5min_count", align: "right", bar: "raw", format: (v, t) => fmtNum(v, t), sort: "late5_count" },
    { index: 5, labelKey: "reports.col.samples", align: "right", samples: true, stat: true, format: (v, t) => fmtNum(v, t) },
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

/** Whether a report's table has statistics-only columns, so its screen
 *  offers the statistics toggle. */
export function hasStatisticsColumns(reportType: string): boolean {
  return SCHEMAS[reportType]?.some((c) => c.stat) ?? false;
}

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
