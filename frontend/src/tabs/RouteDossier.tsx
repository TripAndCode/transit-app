import { lazy } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { ScopeRouteContext } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { destHref, routeHref } from "../routes/destinations";
import { loadRouteAnalysisTab } from "../routes/lazyTabs";

const RouteAnalysisTab = lazy(loadRouteAnalysisTab);

/** One route's page. The route lives in the path and reaches the screens
 *  inside through ScopeRouteContext, never as a `routes` param. A `routes`
 *  param here is a route picker's request: one route moves the page to that
 *  route's path, several hand the selection to the Routes list. The screen
 *  stays mounted across a move, so its sub-tab and map survive it. */
export function RouteDossier() {
  const { routeCode = "" } = useParams();
  const { search } = useLocation();
  const id = useAgencyId();
  if (id == null) return null;
  const requested = (new URLSearchParams(search).get("routes") ?? "").split(",").filter(Boolean);
  if (requested.length > 1) return <Navigate to={destHref(id, "routes", search)} replace />;
  return (
    <ScopeRouteContext value={routeCode}>
      {requested.length === 1 && <Navigate to={routeHref(id, requested[0], search)} replace />}
      <RouteAnalysisTab />
    </ScopeRouteContext>
  );
}
