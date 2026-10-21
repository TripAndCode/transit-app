import { useTranslation } from "react-i18next";
import { useTodayRouteSummary } from "../api/hooks";
import { usePinnedRoutes } from "../api/pinnedRoutes";
import { todayMeanMinutes } from "../api/todayRouteFigures";
import { routeDescription, routeWhereText, useRouteNames } from "../api/useRouteNames";
import { routeHref } from "../routes/destinations";
import { delayRampVar } from "../styles/tokens";
import { PendingNavLink } from "./navPending";
import { RailTooltip } from "./RailTooltip";
import "./sidebarLineMap.css";
import { fixedTenth } from "../utils/format";

/** The badge of a pinned route the static routes do not name yet. */
const PLAIN_BADGE = "\u2022";

/** The routes pinned for the agency, each a link to its page with the Routes
 *  screen's remembered scope, beside today's mean delay. That figure shares
 *  the top bar's query for the summary, and the names the route pickers'
 *  query for the agency's routes list, so neither is fetched twice. */
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
  const names = useRouteNames(id);
  const { data: today } = useTodayRouteSummary(id, { autoRefresh: false });
  if (collapsed && pins.length === 0) return null;
  const title = t("nav.pinned_routes");
  return (
    <nav aria-label={title} className="rail-pins" data-collapsed={collapsed || undefined}>
      {!collapsed && <div className="rail-pins-title">{title}</div>}
      {pins.length === 0 && <p className="rail-pins-empty">{t("nav.pinned_routes_empty")}</p>}
      {pins.map((code) => {
        const parts = names.parts.get(code);
        const name = parts ? routeDescription(parts, t) : t("common.route_code_fallback", { code });
        // The badge holds the route's short code, so the line beside it says
        // where the route goes. A route without a code wears its name's first
        // character instead, and its name goes beside it. The route_code is
        // never the badge: on many feeds it is the opaque route_id.
        const badge = parts?.plate ?? (parts?.line ? Array.from(parts.line)[0] : PLAIN_BADGE);
        const beside = !parts ? "" : parts.plate ? (routeWhereText(parts, t) ?? parts.line ?? "") : (parts.line ?? routeWhereText(parts, t) ?? "");
        const minutes = todayMeanMinutes(today?.routes, code);
        const delay = minutes == null ? null : t("common.minutes_value", { value: fixedTenth(minutes) });
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
                {badge}
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
