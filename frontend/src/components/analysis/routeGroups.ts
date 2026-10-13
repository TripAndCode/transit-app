import type { Route } from "../../api/types";
import { routeNameText, type RouteLabelParts } from "../../api/routeDisplayName";

// Feed data embeds a 系統番号 (pattern number) as a leading token in the
// published route name -- e.g. "14-5 共立ハイツ線", "11-19 共立ハイツ線",
// "14H 共立ハイツ線", or no prefix at all for "共立ハイツ線" itself. Group
// by the name with that leading token stripped, or every pattern of the
// same 路線 becomes its own solo group and the 路線→系統 filter cascade
// narrows nothing.
const LEADING_PATTERN_PREFIX = /^[0-9][0-9A-Za-z-]*\s+/;

/** Labels keyed by route_code, as `useRouteNames` serves them. */
type LabelsByCode = ReadonlyMap<string, RouteLabelParts>;

/** The line a route belongs to: its label's line name, which has the code
 *  split off whichever name field held it and the agency's own name taken
 *  off the front; failing a label, the name with its leading token stripped. */
function baseRouteName(route: Route, labels: LabelsByCode | undefined): string {
  const label = route.route_code ? labels?.get(route.route_code) : undefined;
  if (label) return label.line ?? routeNameText(label);
  const name = route.route_long_name || route.route_short_name || route.route_id;
  const stripped = name.replace(LEADING_PATTERN_PREFIX, "");
  return stripped || name;
}

export function routeGroups(routes: Route[], labels?: LabelsByCode) {
  const groups = new Map<string, Route[]>();
  for (const route of routes) {
    if (!route.route_code) continue;
    const name = baseRouteName(route, labels);
    groups.set(name, [...(groups.get(name) ?? []), route]);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b));
}

export function selectedGroup(routes: Route[], codes: string[], labels?: LabelsByCode): string {
  if (!codes.length) return "";
  return routeGroups(routes, labels).find(([, entries]) => codes.every((code) => entries.some((r) => r.route_code === code)))?.[0] ?? "";
}
