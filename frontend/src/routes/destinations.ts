import { REPORT_TYPE_IDS } from "../tabs/reportTypes";

/** The rail's destinations, in the order it lists them. */
export const DESTINATIONS = ["pulse", "routes", "time", "why", "compare", "live", "reports"] as const;
export type Destination = (typeof DESTINATIONS)[number];

export const ROUTE_TABS = ["summary", "stops", "time", "trips", "reliability", "why"] as const;
export type RouteTab = (typeof ROUTE_TABS)[number];

type ReportTypeId = (typeof REPORT_TYPE_IDS)[number];
export const ROUTES_REPORT_TYPES = ["ranking", "ranking_best", "on_time", "worst_5min"] as const satisfies readonly ReportTypeId[];
export const TIME_REPORT_TYPES = ["trend", "dow_weekday", "dow_weekend", "route_forecast"] as const satisfies readonly ReportTypeId[];
export const WHY_REPORT_TYPES = ["dwell_run"] as const satisfies readonly ReportTypeId[];
export const COMPARE_REPORT_TYPES = ["compare_ranking"] as const satisfies readonly ReportTypeId[];
export const SAVED_REPORT_TYPES = ["council_summary", "delay_certificate"] as const satisfies readonly ReportTypeId[];

/** Where each report type lives. Reports' own documents are picked by
 *  `doc`; every other screen opens the type by `report`. */
const REPORT_HOME: Record<ReportTypeId, { dest: Destination; extra: Record<string, string> }> = {
  ranking: { dest: "routes", extra: { report: "ranking" } },
  ranking_best: { dest: "routes", extra: { report: "ranking_best" } },
  on_time: { dest: "routes", extra: { report: "on_time" } },
  worst_5min: { dest: "routes", extra: { report: "worst_5min" } },
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
 *  filters. An agency switch keeps these even where it drops the rest. */
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
 *  contradict it. */
export function routeHref(agencyId: number | string, code: string, search = "", tab?: RouteTab): string {
  const params = new URLSearchParams(search);
  params.delete("routes");
  return `/agencies/${agencyId}/routes/${encodeURIComponent(code)}${mergeSearch(params.toString(), tab ? { tab } : {})}`;
}

/** The Routes screen for a `routes` selection: one route opens its dossier,
 *  any other selection the list. */
export function routesHref(agencyId: number | string, search = "", tab?: RouteTab): string {
  const routes = (new URLSearchParams(search).get("routes") ?? "").split(",").filter(Boolean);
  return routes.length === 1 ? routeHref(agencyId, routes[0], search, tab) : destHref(agencyId, "routes", search);
}

export function reportHref(agencyId: number | string, reportType: string, search = ""): string {
  if (!isReportType(reportType)) return destHref(agencyId, "pulse", search);
  const home = REPORT_HOME[reportType];
  const params = new URLSearchParams(search);
  params.delete("report");
  return destHref(agencyId, home.dest, params.toString(), home.extra);
}

/** The same destination for another agency. A route dossier lands on the
 *  Routes list instead: the other agency may have no route by that code. */
export function agencySwitchHref(
  agencyId: number | string,
  rest: string | undefined,
  search: string,
  scopeQuery = "",
): string {
  const dest = !rest ? "pulse" : rest.startsWith("routes/") ? "routes" : rest;
  return `/agencies/${agencyId}/${dest}${mergeSearch(scopeQuery, screenParams(search))}`;
}

/** Saved & export picked its view by `view`, or an export `report`; Reports
 *  picks its document by `doc`. */
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
