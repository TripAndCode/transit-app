import type { Route } from "../../api/types";

export function routeGroups(routes: Route[]) {
  const groups = new Map<string, Route[]>();
  for (const route of routes) {
    if (!route.route_code) continue;
    const name = route.route_long_name || route.route_short_name || route.route_id;
    groups.set(name, [...(groups.get(name) ?? []), route]);
  }
  return [...groups].sort(([a], [b]) => a.localeCompare(b));
}

export function selectedGroup(routes: Route[], codes: string[]): string {
  if (!codes.length) return "";
  return routeGroups(routes).find(([, entries]) => codes.every((code) => entries.some((r) => r.route_code === code)))?.[0] ?? "";
}
