import { Navigate, useLocation } from "react-router-dom";
import { readLastAgency } from "../api/lastAgency";

/** Legacy top-level /network route, from before Network became an
 *  agency-scoped sidebar tab. Redirects to the last-selected agency's
 *  Network view if one is known (preserving the query string), otherwise
 *  sends the user through onboarding via "/" — there's no "current agency"
 *  concept for a bare /network hit with no prior selection. */
export function RedirectNetworkToAgencyNetwork() {
  const location = useLocation();
  const lastAgencyId = readLastAgency();
  if (lastAgencyId == null) return <Navigate to="/" replace />;
  return <Navigate to={`/agencies/${lastAgencyId}/network${location.search}`} replace />;
}
