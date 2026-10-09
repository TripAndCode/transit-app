import { Navigate, useLocation } from "react-router-dom";
import { readLastAgency } from "../api/lastAgency";
import { destHref } from "./destinations";

/** Legacy top-level /network route. Redirects to the last-selected agency's
 *  Compare screen by agencies if one is known (preserving the query
 *  string), otherwise sends the user through onboarding via "/" — there's no
 *  "current agency" concept for a bare /network hit with no prior selection. */
export function RedirectNetworkToAgencyNetwork() {
  const location = useLocation();
  const lastAgencyId = readLastAgency();
  if (lastAgencyId == null) return <Navigate to="/" replace />;
  return <Navigate to={destHref(lastAgencyId, "compare", location.search, { by: "agencies" })} replace />;
}
