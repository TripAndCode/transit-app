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
