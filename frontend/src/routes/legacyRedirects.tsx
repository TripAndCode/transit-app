import { Navigate, useLocation, useParams } from "react-router-dom";
import { readLastAgency } from "../api/lastAgency";
import { destHref, reportHref, routesHref, savedTarget, type Destination } from "./destinations";

/** The redirects from retired URLs, each replacing itself with the screen that
 *  serves it and carrying the query string so filters and deep links survive,
 *  until the removal date.
 *
 *  Date after which these redirects and the routes in `main.tsx` that mount
 *  them are deleted. The old paths are what go stale: a bookmark to one that
 *  nobody has opened since then no longer matters. `legacyRedirects.test.tsx`
 *  fails on a scheduled run once the date has passed. */
export const LEGACY_REDIRECTS_REMOVE_AFTER = "2027-05-16";

export function RedirectTo({ dest, extra = {} }: { dest: Destination; extra?: Record<string, string> }) {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={destHref(agencyId, dest, search, extra)} replace />;
}

export function RedirectWhere() {
  const { agencyId = "" } = useParams();
  const { search } = useLocation();
  return <Navigate to={routesHref(agencyId, search)} replace />;
}

const LENS_DEST: Record<string, Destination> = { overview: "pulse", when: "time", why: "why", predict: "pulse" };

function lensTarget(agencyId: string, lens: string, search: string): string {
  if (lens === "where") return routesHref(agencyId, search);
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

/** The bare `/network` bookmark: the last-selected agency's Compare screen by
 *  agencies if one is known, otherwise onboarding via "/", since a bare hit
 *  has no current agency. */
export function RedirectNetworkToAgencyNetwork() {
  const { search } = useLocation();
  const lastAgencyId = readLastAgency();
  if (lastAgencyId == null) return <Navigate to="/" replace />;
  return <Navigate to={destHref(lastAgencyId, "compare", search, { by: "agencies" })} replace />;
}
