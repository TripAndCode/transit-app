import { Navigate, useLocation, useParams } from "react-router-dom";
import { lensHref, mergeSearch, reportHref, type LensId } from "./analysisRoutes";

/** Every pre-workspace URL keeps working: each one replaces itself with its
 *  new home, carrying the query string so filters and deep links survive. */

export function RedirectToLive() {
  const { agencyId } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/agencies/${agencyId}/live${search}`} replace />;
}

export function RedirectToLens({ lens, extra = {} }: { lens: LensId; extra?: Record<string, string> }) {
  const { agencyId } = useParams();
  const { search } = useLocation();
  return <Navigate to={lensHref(agencyId ?? "", lens, search, extra)} replace />;
}

export function RedirectReportsToSaved() {
  const { agencyId } = useParams();
  const { search } = useLocation();
  return <Navigate to={`/agencies/${agencyId}/saved${mergeSearch(search, {})}`} replace />;
}

export function RedirectReportTypeToLens() {
  const { agencyId, reportType } = useParams();
  const { search } = useLocation();
  return <Navigate to={reportHref(agencyId ?? "", reportType ?? "", search)} replace />;
}

export function RedirectForecastToWhen() {
  const { agencyId } = useParams();
  const { search } = useLocation();
  return <Navigate to={reportHref(agencyId ?? "", "route_forecast", search)} replace />;
}
