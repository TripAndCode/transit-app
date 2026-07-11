import { Navigate, useLocation, useParams } from "react-router-dom";

/** Redirects the old standalone Reports URL(s) to their Analysis
 *  equivalent (Phase 1 renamed the tab; this closes the URL gap that
 *  rename deliberately left open), preserving the query string (date
 *  range, filters, etc.) and using `replace` so the dead URL doesn't
 *  linger in browser history. */
export function RedirectReportsToAnalysis() {
  const { agencyId, reportType } = useParams();
  const location = useLocation();
  const target = reportType
    ? `/agencies/${agencyId}/analysis/${reportType}`
    : `/agencies/${agencyId}/analysis`;
  return <Navigate to={`${target}${location.search}`} replace />;
}

/** Redirects the old standalone Forecast URL to the new Route-forecast
 *  entry inside Analysis (Phase 2 folded Forecast's content in; this
 *  closes the URL gap that phase deliberately left open). The old
 *  ForecastTab had no URL-encoded route selection of its own (it was
 *  local useState, never persisted) — so there's no route param to carry
 *  over; landing on the agency-wide view is the complete equivalent of
 *  what the old bare /forecast URL showed. */
export function RedirectForecastToAnalysis() {
  const { agencyId } = useParams();
  const location = useLocation();
  return <Navigate to={`/agencies/${agencyId}/analysis/route_forecast${location.search}`} replace />;
}
