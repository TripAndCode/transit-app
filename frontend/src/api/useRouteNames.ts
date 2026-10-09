import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useRoutes } from "./hooks";
import type { Route } from "./types";
import { routeDisplayName, routeHeading } from "./routeDisplayName";

/** A route's name and where it goes ("A1 国道・古川線 · for 青森駅"); the
 *  name alone when its headsign says nothing usable. See routeDisplayName
 *  for the name's fallback order. */ // i18n-ignore: JSDoc example
export function routeLabel(route: Route, t: TFunction): string {
  const name = routeDisplayName(route);
  const heading = routeHeadingText(route, t);
  return heading ? t("common.route_label", { name, heading }) : name;
}

/** Where a route goes ("for 青森駅"), or null when its headsigns say
 *  nothing usable. */ // i18n-ignore: JSDoc example
export function routeHeadingText(route: Route, t: TFunction): string | null {
  const heading = routeHeading(route);
  if (!heading) return null;
  return heading.from
    ? t("common.route_heading_from_to", { from: heading.from, to: heading.to })
    : t("common.route_heading_to", { to: heading.to });
}

/** Build a route_code → routeLabel lookup for the agency, so variants of one
 *  line read apart without their GTFS ids. */
export function useRouteNames(agencyId: number | null): {
  data: Map<string, string>;
  isLoading: boolean;
  format: (route_code: string | null | undefined) => string;
} {
  const { t } = useTranslation();
  const { data, isLoading } = useRoutes(agencyId);
  const map = new Map<string, string>();
  if (data) {
    for (const r of data) {
      if (r.route_code) map.set(r.route_code, routeLabel(r, t));
    }
  }

  function format(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    return map.get(String(route_code)) ?? t("common.route_code_fallback", { code: route_code });
  }

  return { data: map, isLoading, format };
}
