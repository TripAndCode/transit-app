import type { TFunction } from "i18next";
import { reportLabel } from "./analysis/reportGroups";
import {
  COMPARE_MODES,
  DESTINATIONS,
  REPORTS_VIEWS,
  ROUTES_REPORT_TYPES,
  TIME_REPORT_TYPES,
  compareMode,
  pickReport,
  reportsView,
  type Destination,
} from "../routes/destinations";
import { screenOf } from "../api/screenScope";

/** One of the reports or boards a destination's own screen switches between,
 *  reached from the rail with `extra` on top of the screen's remembered
 *  scope. */
export type RailStop = {
  id: string;
  label: (t: TFunction) => string;
  extra: Readonly<Record<string, string>>;
};

function reportStops(types: readonly string[]): RailStop[] {
  return types.map((id) => ({ id, label: (t) => reportLabel(t, id), extra: { report: id } }));
}

/** The stops each destination opens under itself on the rail, in the order
 *  its own screen lists them. A screen with a single view has none. */
export const RAIL_STOPS: Partial<Record<Destination, readonly RailStop[]>> = {
  routes: reportStops(ROUTES_REPORT_TYPES),
  time: reportStops(TIME_REPORT_TYPES),
  compare: COMPARE_MODES.map(({ mode, labelKey }) => ({ id: mode, label: (t) => t(labelKey), extra: { by: mode } })),
  reports: REPORTS_VIEWS.map(({ view, labelKey, extra }) => ({ id: view, label: (t) => t(labelKey), extra })),
};

/** The stop a destination's own page shows, by the rule its screen picks
 *  with: Routes falls back to `sort`, as RoutesIndex does. */
export function currentStop(dest: Destination, search: string): string | null {
  const params = new URLSearchParams(search);
  switch (dest) {
    case "routes":
      return pickReport(params.get("report"), ROUTES_REPORT_TYPES, params.get("sort"));
    case "time":
      return pickReport(params.get("report"), TIME_REPORT_TYPES);
    case "compare":
      return compareMode(params.get("by"));
    case "reports":
      return reportsView(params.get("doc"), params.get("report"));
    default:
      return null;
  }
}

function isDestination(screen: string): screen is Destination {
  return (DESTINATIONS as readonly string[]).includes(screen);
}

const ROUTE_DOSSIER_PATH = /^\/agencies\/[^/]+\/routes\/[^/]+/;

/** The destination a URL belongs to on the rail, and the stop it shows when
 *  it is that destination's own page. A route dossier belongs to Routes but
 *  is none of its stops. */
export function railPlace(url: string): { dest: Destination; stop: string | null } | null {
  const [path, query = ""] = url.split("?");
  const own = screenOf(path);
  if (own && isDestination(own.screen)) return { dest: own.screen, stop: currentStop(own.screen, query) };
  return ROUTE_DOSSIER_PATH.test(path) ? { dest: "routes", stop: null } : null;
}
