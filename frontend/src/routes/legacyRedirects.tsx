import { Navigate, useLocation, useParams } from "react-router-dom";

/** Redirect old Reports URLs to the equivalent Analysis route. */
export function RedirectReportsToAnalysis() {
  const { agencyId, reportType } = useParams();
  const location = useLocation();
  const target = reportType
    ? `/agencies/${agencyId}/analysis/${reportType}`
    : `/agencies/${agencyId}/analysis`;
  return <Navigate to={`${target}${location.search}`} replace />;
}

/** Redirect the old Forecast URL to the Analysis route-forecast view. */
export function RedirectForecastToAnalysis() {
  const { agencyId } = useParams();
  const location = useLocation();
  return <Navigate to={`/agencies/${agencyId}/analysis/route_forecast${location.search}`} replace />;
}

/** Redirect the former standalone live board to the unified Operations map. */
export function RedirectLiveToOperations() {
  const { agencyId } = useParams();
  const location = useLocation();
  return <Navigate to={`/agencies/${agencyId}/operations${location.search}`} replace />;
}

/** Redirect the pre-rename Overview URL (the map tab's former path) to the
 *  renamed Operations route. */
export function RedirectOverviewToOperations() {
  const { agencyId } = useParams();
  const location = useLocation();
  return <Navigate to={`/agencies/${agencyId}/operations${location.search}`} replace />;
}

/** Redirect the pre-rename Map URL to the renamed Operations route. Kept
 *  distinct from `RedirectOverviewToOperations` (rather than reused under one
 *  name) so each legacy entry point's intent stays traceable at the call
 *  site in `main.tsx`. */
export function RedirectMapToOperations() {
  const { agencyId } = useParams();
  const location = useLocation();
  return <Navigate to={`/agencies/${agencyId}/operations${location.search}`} replace />;
}
