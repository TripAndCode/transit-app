import type { Route } from "./types";

/**
 * Prefer route_short_name, then route_long_name, before falling back to the
 * raw route_id (which duplicates the parenthesised code inline and reads
 * worse than either GTFS name field). Mirrors the backend's own
 * COALESCE(NULLIF(short,''), NULLIF(long,''), code) order in
 * api/routers/reports.py's forecast_overview route-label query — empty
 * strings (not just null/undefined) count as "absent" on both sides.
 *
 * Single source of truth for this 3-way fallback: any caller should use
 * this instead of re-deriving the order, or the labels drift apart.
 */
export function routeDisplayName(route: Pick<Route, "route_short_name" | "route_long_name" | "route_id">): string {
  return route.route_short_name || route.route_long_name || route.route_id;
}

/** Where a route's trips go, read from its one headsign: an origin when the
 *  sign shows one ("明の星→青森駅"), and the destination. Null when there is
 *  no headsign, or several and no telling which one the label is for.
 *
 *  Agencies decorate headsigns: a route-code prefix glued to the place name
 *  ("A1明の星…"), the line name in trailing parentheses, a "行"/"行き" suffix.
 *  Those are stripped so the label can add the direction in its own words;
 *  the prefix only when it runs straight into non-Latin text, so a Latin
 *  headsign is left alone. */ // i18n-ignore: JSDoc examples
export function routeHeading(route: Pick<Route, "trip_headsigns">): { from: string | null; to: string } | null {
  const signs = route.trip_headsigns ?? [];
  if (signs.length !== 1) return null;
  const sign = signs[0]
    .trim()
    .replace(/[（(][^（()）]*[)）]\s*$/u, "")
    .replace(/^[A-Za-z]+\d*(?=\P{ASCII})/u, "")
    .trim();
  const parts = sign.split(/\s*(?:→|->)\s*/u).filter(Boolean);
  if (parts.length === 0) return null;
  const to = parts[parts.length - 1].replace(/(行き|行)$/u, "") || parts[parts.length - 1]; // i18n-ignore: a headsign suffix to strip, not display text
  return { from: parts.length > 1 ? parts[0] : null, to };
}
