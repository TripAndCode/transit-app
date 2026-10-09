import { useTranslation } from "react-i18next";
import { useRoutes, useTodayRouteSummary } from "../api/hooks";
import { usePinnedRoutes } from "../api/pinnedRoutes";
import type { RouteSummary } from "../api/types";
import { routeHeadingText, routeLabel } from "../api/useRouteNames";
import { routeHref } from "../routes/destinations";
import { delayRampVar } from "../styles/tokens";
import { PendingNavLink } from "./navPending";
import { RailTooltip } from "./RailTooltip";
import "./sidebarLineMap.css";

/** Today's mean delay for a route, in minutes, weighted by trips across the
 *  service types it runs as; null when it has run no observed trip today. */
function todayMeanMinutes(routes: readonly RouteSummary[] | undefined, code: string): number | null {
  let trips = 0;
  let weighted = 0;
  for (const row of routes ?? []) {
    if (row.route_code !== code || row.trips_observed <= 0) continue;
    trips += row.trips_observed;
    weighted += row.avg_delay_sec * row.trips_observed;
  }
  return trips > 0 ? weighted / trips / 60 : null;
}

/** The routes pinned for the agency, each a link to its page with the Routes
 *  screen's remembered scope, beside today's mean delay. That figure comes
 *  from the summary the top bar already fetches; the names come from the
 *  agency's routes list, cached alongside the route pickers'. */
export function SidebarPinnedRoutes({
  collapsed,
  agencyId,
  screenQuery,
}: {
  collapsed: boolean;
  agencyId: string | number;
  screenQuery: (agencyId: string | number, screen: string) => string;
}) {
  const { t } = useTranslation();
  const pins = usePinnedRoutes(agencyId);
  const id = pins.length > 0 ? Number(agencyId) : null;
  const { data: routes } = useRoutes(id);
  const { data: today } = useTodayRouteSummary(id, { autoRefresh: false });
  if (collapsed && pins.length === 0) return null;
  const title = t("nav.pinned_routes");
  return (
    <nav aria-label={title} className="rail-pins" data-collapsed={collapsed || undefined}>
      {!collapsed && <div className="rail-pins-title">{title}</div>}
      {pins.length === 0 && <p className="rail-pins-empty">{t("nav.pinned_routes_empty")}</p>}
      {pins.map((code) => {
        const route = routes?.find((r) => r.route_code === code);
        const name = route ? routeLabel(route, t) : t("common.route_code_fallback", { code });
        // The badge already shows the code, so the line beside it says
        // where the route goes, falling back to its full label.
        const beside = route ? (routeHeadingText(route, t) ?? name) : "";
        const minutes = todayMeanMinutes(today?.routes, code);
        const delay = minutes == null ? null : t("common.minutes_value", { value: minutes.toFixed(1) });
        const label = delay == null ? name : t("nav.pinned_route_label", { route: name, delay });
        return (
          <RailTooltip key={code} collapsed={collapsed} label={label}>
            <PendingNavLink
              to={routeHref(agencyId, code, screenQuery(agencyId, "routes"))}
              className="rail-pin"
              aria-label={label}
              spinner={false}
            >
              <span className="rail-pin-code num" aria-hidden="true">
                {code}
              </span>
              {!collapsed && (
                <span className="rail-pin-name" aria-hidden="true">
                  {beside}
                </span>
              )}
              {!collapsed && minutes != null && (
                <span className="rail-pin-delay num" aria-hidden="true">
                  <i className="rail-pin-swatch" style={{ background: delayRampVar(minutes) }} />
                  {delay}
                </span>
              )}
            </PendingNavLink>
          </RailTooltip>
        );
      })}
    </nav>
  );
}
