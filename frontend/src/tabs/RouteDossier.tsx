import { lazy } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";
import { useScope } from "../api/scope";
import { useAgencyId } from "../api/useAgencyId";
import { destHref, mergeSearch } from "../routes/destinations";
import { loadRouteAnalysisTab } from "../routes/lazyTabs";

const RouteAnalysisTab = lazy(loadRouteAnalysisTab);

/** One route's dossier. The route lives in the path, and the scope's
 *  `routes` mirrors it for the screens inside. A route picker inside writes
 *  `routes`: one different route moves the dossier to that route's path, and
 *  several hand the selection to the Routes list. A URL cannot tell a
 *  cleared picker from a fresh arrival, so an empty `routes` is re-seeded
 *  from the path. */
export function RouteDossier() {
  const { routeCode = "" } = useParams();
  const { search } = useLocation();
  const [scope] = useScope();
  const id = useAgencyId();
  if (id == null) return null;
  const inScope = scope.routes;
  if (inScope.length > 1) return <Navigate to={destHref(id, "routes", search)} replace />;
  const code = inScope[0] ?? routeCode;
  if (inScope.length === 0 || code !== routeCode) {
    const to = `/agencies/${id}/routes/${encodeURIComponent(code)}${mergeSearch(search, { routes: code })}`;
    return <Navigate to={to} replace />;
  }
  return <RouteAnalysisTab />;
}
