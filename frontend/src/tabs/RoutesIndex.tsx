import { lazy, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useRoutes } from "../api/hooks";
import { useAgencyId } from "../api/useAgencyId";
import { useRouteNames } from "../api/useRouteNames";
import { ROUTES_REPORT_TYPES, routeHref } from "../routes/destinations";
import { loadAnalysisTab } from "../routes/lazyTabs";

const AnalysisTab = lazy(loadAnalysisTab);

/** The Routes list: a route opener above the route ranking reports. `sort`
 *  names the report that opens when none is chosen. The opener leaves only
 *  on Open: some browsers fire `change` while arrow keys browse a closed
 *  select, and a keyboard user must be able to look before going. */
export function RoutesIndex() {
  const { t } = useTranslation();
  const id = useAgencyId();
  const navigate = useNavigate();
  const { search } = useLocation();
  const [params] = useSearchParams();
  const routes = useRoutes(id).data ?? [];
  const names = useRouteNames(id);
  const selectId = useId();
  const [chosen, setChosen] = useState("");
  if (id == null) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (chosen) navigate(routeHref(id, chosen, search));
        }}
        style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 12 }}
      >
        <label htmlFor={selectId} style={{ fontSize: "var(--text-sm)", color: "var(--text-secondary)" }}>
          {t("routesIndex.open_route")}
        </label>
        <select
          id={selectId}
          value={chosen}
          onChange={(e) => setChosen(e.target.value)}
          style={{ maxWidth: "100%" }}
        >
          <option value="" disabled>
            {t("routesIndex.open_route_placeholder")}
          </option>
          {routes.map((r) =>
            r.route_code ? (
              <option key={r.route_code} value={r.route_code}>
                {names.format(r.route_code)}
              </option>
            ) : null,
          )}
        </select>
        <button type="submit" disabled={!chosen}>
          {t("routesIndex.open")}
        </button>
      </form>
      <div style={{ flex: 1, minHeight: 0 }}>
        <AnalysisTab reportTypes={ROUTES_REPORT_TYPES} defaultReport={params.get("sort")} />
      </div>
    </div>
  );
}
