import { REPORT_TYPE_IDS } from "../tabs/reportTypes";

/** The rail's destinations, in the order it lists them. */
export const DESTINATIONS = ["pulse", "routes", "time", "why", "compare", "live", "reports"] as const;
export type Destination = (typeof DESTINATIONS)[number];

type ReportTypeId = (typeof REPORT_TYPE_IDS)[number];
/** The route lists the Routes table stands in for: each opens it sorted the
 *  way that list was ordered. */
export const ROUTES_REPORT_TYPES = ["ranking", "ranking_best", "on_time", "worst_5min"] as const satisfies readonly ReportTypeId[];
export const TIME_REPORT_TYPES = ["trend", "dow_weekday", "dow_weekend", "route_forecast"] as const satisfies readonly ReportTypeId[];
export const WHY_REPORT_TYPES = ["dwell_run"] as const satisfies readonly ReportTypeId[];
export const COMPARE_REPORT_TYPES = ["compare_ranking"] as const satisfies readonly ReportTypeId[];
export const SAVED_REPORT_TYPES = ["council_summary", "delay_certificate"] as const satisfies readonly ReportTypeId[];

/** The figures the Routes table sorts by, in the order its control lists
 *  them: the API's `sort` values (api/routers/reports.py, route_table). */
export const ROUTE_TABLE_SORTS = ["avg", "on_time", "late5_share", "late5_count"] as const;
export type RouteTableSort = (typeof ROUTE_TABLE_SORTS)[number];
export const ROUTE_TABLE_SORT_LABELS = {
  avg: "routeTable.sort.avg",
  on_time: "routeTable.sort.on_time",
  late5_share: "routeTable.sort.late5_share",
  late5_count: "routeTable.sort.late5_count",
} as const satisfies Record<RouteTableSort, string>;
/** `worst` lists the routes that need attention first; `best` the other end. */
export type RouteTableOrder = "worst" | "best";
export type RouteTableView = { sort: RouteTableSort; order: RouteTableOrder };

const ROUTE_REPORT_VIEWS: Record<(typeof ROUTES_REPORT_TYPES)[number], RouteTableView> = {
  ranking: { sort: "avg", order: "worst" },
  ranking_best: { sort: "avg", order: "best" },
  on_time: { sort: "on_time", order: "worst" },
  worst_5min: { sort: "late5_count", order: "worst" },
};

// The one former route list with an order of its own to choose. Every other
// list's order was fixed, so an `order` left over beside one of them, from a
// visit to the on-time list, says nothing about it.
const ORDERED_ROUTE_REPORT = "on_time";

function routeReportView(value: string | null): RouteTableView | null {
  return value != null && Object.hasOwn(ROUTE_REPORT_VIEWS, value)
    ? ROUTE_REPORT_VIEWS[value as keyof typeof ROUTE_REPORT_VIEWS]
    : null;
}

function isRouteTableSort(value: string | null): value is RouteTableSort {
  return (ROUTE_TABLE_SORTS as readonly (string | null)[]).includes(value);
}

/** The sort and order a Routes URL asks the table for. A `report` naming one
 *  of the route lists, or a `sort` naming one, opens the table the way that
 *  list was ordered, so a link from before the table keeps its meaning;
 *  `report` wins over `sort`, as it did when it picked the list. `order`
 *  turns the view around only where it was chosen for it: beside a table
 *  sort, or beside the on-time list, the one list that had the choice. */
export function routeTableView(search: string | URLSearchParams): RouteTableView {
  const params = new URLSearchParams(search);
  const report = params.get("report");
  const sortParam = params.get("sort");
  const fromReport = routeReportView(report);
  const [view, ordered] = fromReport
    ? [fromReport, report === ORDERED_ROUTE_REPORT]
    : isRouteTableSort(sortParam)
      ? [{ sort: sortParam, order: "worst" as const }, true]
      : [routeReportView(sortParam) ?? { sort: ROUTE_TABLE_SORTS[0], order: "worst" as const }, false];
  const order = params.get("order");
  return ordered && (order === "best" || order === "worst") ? { ...view, order } : view;
}

/** The report a screen hosting `reportTypes` opens: the requested one when
 *  the screen hosts it, else `defaultReport` when it does, else the first,
 *  so a stale link from another screen never opens a report this screen
 *  doesn't host. */
export function pickReport(
  requested: string | null,
  reportTypes: readonly string[],
  defaultReport?: string | null,
): string | null {
  const fallback = defaultReport != null && reportTypes.includes(defaultReport) ? defaultReport : (reportTypes[0] ?? null);
  return requested != null && reportTypes.includes(requested) ? requested : fallback;
}

/** Compare's two boards, picked by `by`. */
export const COMPARE_MODES = [
  { mode: "periods", labelKey: "compare.mode_periods" },
  { mode: "agencies", labelKey: "compare.mode_agencies" },
] as const;
export type CompareMode = (typeof COMPARE_MODES)[number]["mode"];

/** `by=agencies` is the agencies board; every other `by` compares periods. */
export function compareMode(by: string | null): CompareMode {
  return by === "agencies" ? "agencies" : "periods";
}

/** Reports' three views, and the `doc` each opens with. */
export const REPORTS_VIEWS = [
  { view: "summary", labelKey: "saved.view_summary", extra: {} },
  { view: "saved", labelKey: "saved.view_saved", extra: { doc: "saved" } },
  { view: "reports", labelKey: "saved.view_reports", extra: { doc: "council" } },
] as const satisfies readonly { view: string; labelKey: string; extra: Record<string, string> }[];
export type ReportsView = (typeof REPORTS_VIEWS)[number]["view"];

/** An export document's `report` or `doc` opens the documents view,
 *  `doc=saved` the saved analyses, and anything else the period summary. */
export function reportsView(doc: string | null, report: string | null): ReportsView {
  if ((SAVED_REPORT_TYPES as readonly string[]).includes(report ?? "")) return "reports";
  if (doc === "council" || doc === "certificate") return "reports";
  return doc === "saved" ? "saved" : "summary";
}

/** Where each report type lives. Reports' own documents are picked by
 *  `doc`, the Routes table's lists by `sort` (and `order`), and every other
 *  screen opens the type by `report`. */
const REPORT_HOME: Record<ReportTypeId, { dest: Destination; extra: Record<string, string> }> = {
  ranking: { dest: "routes", extra: { sort: "avg" } },
  ranking_best: { dest: "routes", extra: { sort: "avg", order: "best" } },
  on_time: { dest: "routes", extra: { sort: "on_time" } },
  worst_5min: { dest: "routes", extra: { sort: "late5_count" } },
  trend: { dest: "time", extra: { report: "trend" } },
  dow_weekday: { dest: "time", extra: { report: "dow_weekday" } },
  dow_weekend: { dest: "time", extra: { report: "dow_weekend" } },
  route_forecast: { dest: "time", extra: { report: "route_forecast" } },
  dwell_run: { dest: "why", extra: { report: "dwell_run" } },
  compare_ranking: { dest: "compare", extra: { by: "periods", report: "compare_ranking" } },
  council_summary: { dest: "reports", extra: { doc: "council" } },
  delay_certificate: { dest: "reports", extra: { doc: "certificate" } },
};

function isReportType(value: string): value is ReportTypeId {
  return Object.hasOwn(REPORT_HOME, value);
}

export function mergeSearch(search: string, extra: Record<string, string>): string {
  const params = new URLSearchParams(search);
  for (const [k, v] of Object.entries(extra)) params.set(k, v);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/** The params that pick which screen a URL shows rather than what it
 *  filters. An agency switch keeps these even where it drops the rest.
 *  `report` and `sort` are dropped on purpose: they name a report and an
 *  ordering of the previous agency's data, and the destination's default
 *  report is the right landing for the new one. */
export function screenParams(search: string): Record<string, string> {
  const params = new URLSearchParams(search);
  const kept: Record<string, string> = {};
  for (const key of ["by", "doc"]) {
    const value = params.get(key);
    if (value) kept[key] = value;
  }
  return kept;
}

export function destHref(
  agencyId: number | string,
  dest: Destination,
  search = "",
  extra: Record<string, string> = {},
): string {
  return `/agencies/${agencyId}/${dest}${mergeSearch(search, extra)}`;
}

/** A route dossier's route lives in its path; a `routes` param would only
 *  contradict it. The params that picked another screen's report, document
 *  or ranking coverage and order (`report`, `sort`, `doc`, `by`, `sparse`,
 *  `order`), and the compare bars' shown `period`, mean nothing on a route's
 *  page either, nor does the path (`variant`) picked on another route's. */
export function routeHref(agencyId: number | string, code: string, search = ""): string {
  const params = new URLSearchParams(search);
  for (const key of ["routes", "report", "sort", "doc", "by", "sparse", "order", "period", "variant"]) params.delete(key);
  return `/agencies/${agencyId}/routes/${encodeURIComponent(code)}${mergeSearch(params.toString(), {})}`;
}

/** The Routes screen for a `routes` selection: one route opens its dossier,
 *  any other selection the list. */
export function routesHref(agencyId: number | string, search = ""): string {
  const routes = (new URLSearchParams(search).get("routes") ?? "").split(",").filter(Boolean);
  return routes.length === 1 ? routeHref(agencyId, routes[0], search) : destHref(agencyId, "routes", search);
}

/** The screen a report type opens on, or null for an unknown type. */
export function reportDestination(reportType: string): Destination | null {
  return isReportType(reportType) ? REPORT_HOME[reportType].dest : null;
}

/** A report's screen, opened as the report asks. The params that picked a
 *  report or the Routes table's sort and order go, so the new report's own
 *  take their place. The on-time list keeps an `order`: a former on-time
 *  link's `order=best` still means the same end of the same list. */
export function reportHref(agencyId: number | string, reportType: string, search = ""): string {
  if (!isReportType(reportType)) return destHref(agencyId, "pulse", search);
  const home = REPORT_HOME[reportType];
  const params = new URLSearchParams(search);
  for (const key of ["report", "sort"]) params.delete(key);
  if (reportType !== ORDERED_ROUTE_REPORT) params.delete("order");
  return destHref(agencyId, home.dest, params.toString(), home.extra);
}

/** The same destination for another agency. Route codes and stop ids
 *  belong to one agency, so the scope loses them, and a route page lands on
 *  the Routes list instead. */
export function agencySwitchHref(
  agencyId: number | string,
  rest: string | undefined,
  search: string,
  scopeQuery = "",
): string {
  const dest = !rest ? "pulse" : rest.startsWith("routes/") ? "routes" : rest;
  const scope = new URLSearchParams(scopeQuery);
  scope.delete("routes");
  scope.delete("stop");
  return `/agencies/${agencyId}/${dest}${mergeSearch(scope.toString(), screenParams(search))}`;
}

/** Reports picks its document by `doc`. A legacy `view` maps onto it
 *  (saved → saved, reports → council), and a legacy export `report` opens
 *  that report's document. */
export function savedTarget(agencyId: number | string, search: string): string {
  const params = new URLSearchParams(search);
  const view = params.get("view");
  const report = params.get("report");
  params.delete("view");
  if (report === "council_summary" || report === "delay_certificate") {
    return reportHref(agencyId, report, params.toString());
  }
  const doc = view === "saved" ? "saved" : view === "reports" ? "council" : null;
  return destHref(agencyId, "reports", params.toString(), doc ? { doc } : {});
}
