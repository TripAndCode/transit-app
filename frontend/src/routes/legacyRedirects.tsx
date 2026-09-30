import { Navigate, useLocation, useParams } from "react-router-dom";
import { destHref, reportHref, routeHref, type Destination } from "./destinations";

/** Every earlier URL keeps working: each one replaces itself with its v2
 *  screen, carrying the query string so filters and deep links survive. */

export function RedirectTo({ dest, extra = {} }: { dest: Destination; extra?: Record<string, string> }) {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={destHref(agencyId, dest, search, extra)} replace />;
}

function whereTarget(agencyId: string, search: string): string {
  const routes = (new URLSearchParams(search).get("routes") ?? "").split(",").filter(Boolean);
  return routes.length === 1 ? routeHref(agencyId, routes[0], search, "stops") : destHref(agencyId, "routes", search);
}

export function RedirectWhere() {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={whereTarget(agencyId, search)} replace />;
}

const LENS_DEST: Record<string, Destination> = { overview: "pulse", when: "time", why: "why", predict: "pulse" };

function lensTarget(agencyId: string, lens: string, search: string): string {
  if (lens === "where") return whereTarget(agencyId, search);
  if (lens === "rider") return destHref(agencyId, "routes", search, { sort: "on_time" });
  if (lens === "compare") {
    const params = new URLSearchParams(search);
    const by = params.get("mode") === "agencies" ? "agencies" : "periods";
    params.delete("mode");
    return destHref(agencyId, "compare", params.toString(), { by });
  }
  if (Object.hasOwn(LENS_DEST, lens)) return destHref(agencyId, LENS_DEST[lens], search);
  // A report type in this segment opens the screen hosting it; reportHref
  // sends any other segment to Pulse.
  return reportHref(agencyId, lens, search);
}

export function RedirectAnalysisLens() {
  const { agencyId = "", lens = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={lensTarget(agencyId, lens, search)} replace />;
}

export function RedirectReportType() {
  const { agencyId = "", reportType = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={reportHref(agencyId, reportType, search)} replace />;
}

/** Saved & export picked its view by `view`, or an export `report`; Reports
 *  picks its document by `doc`. */
export function savedTarget(agencyId: string, search: string): string {
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

export function RedirectSaved() {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={savedTarget(agencyId, search)} replace />;
}

export function RedirectForecast() {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={reportHref(agencyId, "route_forecast", search)} replace />;
}
