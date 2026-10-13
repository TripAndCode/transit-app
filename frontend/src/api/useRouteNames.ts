import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useAgencies, useRoutes } from "./hooks";
import type { Route } from "./types";
import {
  routeLabelPartsByCode,
  routeNameText,
  type RouteLabelParts,
  type RouteQualifier,
  type RouteTerminals,
} from "./routeDisplayName";
import { FILTER_SEPARATOR } from "../utils/format";

/** A route's terminals as text: "A → B", "A ⇄ B", or a loop from its origin. */
export function routeTerminalsText(terminals: RouteTerminals, t: TFunction): string {
  const { from, to, loop, bidirectional } = terminals;
  if (loop || to == null) return t("common.route_loop", { origin: from });
  return t(bidirectional ? "common.route_ends_both" : "common.route_ends", { from, to });
}

export function routeQualifierText(qualifier: RouteQualifier, t: TFunction): string {
  return qualifier.via ? t("common.route_via", { via: qualifier.text }) : qualifier.text;
}

/** The label's second line as text, or null when nothing says where the
 *  route runs: its terminals and via point, then its qualifier. */
export function routeWhereText(parts: RouteLabelParts, t: TFunction): string | null {
  const pieces = [
    parts.terminals && routeTerminalsText(parts.terminals, t),
    parts.terminals?.via && t("common.route_via", { via: parts.terminals.via }),
    parts.qualifier && routeQualifierText(parts.qualifier, t),
  ].filter((piece): piece is string => !!piece);
  return pieces.length ? pieces.join(FILTER_SEPARATOR) : null;
}

/** The label and where the route runs, on one line, for a sentence or an
 *  `<option>` that has to tell a route apart from its variants. */
export function routeDescription(parts: RouteLabelParts, t: TFunction): string {
  const name = routeNameText(parts);
  const where = routeWhereText(parts, t);
  return where ? t("common.route_label", { name, heading: where }) : name;
}

export type RouteNames = {
  /** route_code → the plate and line name alone ("70-3 あさひが丘線"), which
   *  variants of one line share. */ // i18n-ignore: JSDoc example
  data: Map<string, string>;
  /** route_code → the label's parts, which `RouteLabel` draws. */
  parts: Map<string, RouteLabelParts>;
  isLoading: boolean;
  /** The agency's own name, which `RouteLabel` strips from a payload's name too. */
  agencyName: string | null;
  /** The one-line name, with where the route runs added when another route
   *  shares the name; "Route <code>" when the static routes lack the code.
   *  One parameter only: it is handed to `Array.map` as a callback. */
  format: (route_code: string | null | undefined) => string;
  /** The one-line name and where the route runs, with `format`'s fallback. */
  describe: (route_code: string | null | undefined) => string;
};

/** The lookups `useRouteNames` serves, from an agency's static routes. */
export function buildRouteNames(
  routes: Route[] | undefined,
  t: TFunction,
  { isLoading = false, agencyName = null }: { isLoading?: boolean; agencyName?: string | null } = {},
): RouteNames {
  const parts = routeLabelPartsByCode(routes ?? [], agencyName);
  const names = new Map([...parts].map(([code, p]) => [code, routeNameText(p)]));

  function fallback(route_code: string): string {
    return t("common.route_code_fallback", { code: route_code });
  }

  function format(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    const p = parts.get(String(route_code));
    if (!p) return fallback(String(route_code));
    return p.ambiguous ? routeDescription(p, t) : routeNameText(p);
  }

  function describe(route_code: string | null | undefined): string {
    if (!route_code) return "—";
    const p = parts.get(String(route_code));
    return p ? routeDescription(p, t) : fallback(String(route_code));
  }

  return { data: names, parts, isLoading, agencyName, format, describe };
}

/** Build route_code → label lookups for the agency, so a route reads by its
 *  code and line name instead of its GTFS id. The agency's own name is
 *  passed along because some feeds open every line name with it. */
export function useRouteNames(agencyId: number | null): RouteNames {
  const { t } = useTranslation();
  const { data, isLoading } = useRoutes(agencyId);
  const { data: agencies } = useAgencies();
  const agencyName = agencies?.find((a) => a.agency_id === agencyId)?.agency_name ?? null;
  return buildRouteNames(data, t, { isLoading, agencyName });
}
