import type { Route } from "../../api/types";
import { routeNameText, type RouteLabelParts } from "../../api/routeDisplayName";

/** Labels keyed by route_code, as `useRouteNames` serves them. */
type LabelsByCode = ReadonlyMap<string, RouteLabelParts>;

/** The line a route belongs to: its label's line name. Feeds lead their
 *  names with a pattern code ("14-5 共立ハイツ線", "14H 共立ハイツ線"), which the
 *  label has split off whichever name field held it, so every pattern of one
 *  路線 lands in one group and the 路線→系統 cascade can narrow. */ // i18n-ignore: JSDoc examples
function baseRouteName(code: string, routeId: string, labels: LabelsByCode): string {
  const label = labels.get(code);
  return label ? (label.line ?? routeNameText(label)) : routeId;
}

export function routeGroups(routes: Route[], labels: LabelsByCode) {
  const groups = new Map<string, Route[]>();
  for (const route of routes) {
    if (!route.route_code) continue;
    const name = baseRouteName(route.route_code, route.route_id, labels);
    groups.set(name, [...(groups.get(name) ?? []), route]);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b));
}

export function selectedGroup(routes: Route[], codes: string[], labels: LabelsByCode): string {
  if (!codes.length) return "";
  return routeGroups(routes, labels).find(([, entries]) => codes.every((code) => entries.some((r) => r.route_code === code)))?.[0] ?? "";
}

const PLATE_ORDER = new Intl.Collator(undefined, { numeric: true });

/** The distinct route numbers (plates) of a line's routes, in number order:
 *  a line's name alone cannot tell apart two lines a feed spells alike
 *  (くすの木台線 and くすのき台線 are different routes), but their numbers do. */ // i18n-ignore: JSDoc example
export function groupPlates(routes: Route[], labels: LabelsByCode): string[] {
  const plates = new Set<string>();
  for (const route of routes) {
    const plate = route.route_code ? labels.get(route.route_code)?.plate : null;
    if (plate) plates.add(plate);
  }
  return [...plates].sort((a, b) => PLATE_ORDER.compare(a, b));
}
