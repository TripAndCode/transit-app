import type { ComponentType } from "react";
import type { Destination } from "./destinations";

/**
 * The one place the routed tabs are dynamically imported.
 *
 * `main.tsx` and the destination screens build their `React.lazy` components
 * from these loaders, and the sidebar prefetches through the same ones on
 * hover/focus. That shared identity is the
 * point: the bundler keys a chunk by the import expression, so a prefetch
 * written as a second `import()` of the same module would warm a chunk the
 * route then never uses.
 *
 * Each loader maps the module's named export onto the default-export shape
 * `React.lazy` expects.
 */
type TabLoader = () => Promise<{ default: ComponentType<Record<string, never>> }>;

export const loadOverviewTab: TabLoader = () =>
  import("../tabs/OverviewTab").then((m) => ({ default: m.OverviewTab }));
export const loadMapTab: TabLoader = () => import("../tabs/MapTab").then((m) => ({ default: m.MapTab }));
export const loadAskTab: TabLoader = () => import("../tabs/AskTab").then((m) => ({ default: m.AskTab }));
export const loadAnalysisTab = () =>
  import("../tabs/AnalysisTab").then((m) => ({ default: m.AnalysisTab }));
export const loadRouteAnalysisTab: TabLoader = () =>
  import("../tabs/RouteAnalysisTab").then((m) => ({ default: m.RouteAnalysisTab }));
export const loadReportsHomeTab: TabLoader = () =>
  import("../tabs/ReportsHomeTab").then((m) => ({ default: m.ReportsHomeTab }));
export const loadNetworkTab: TabLoader = () =>
  import("../tabs/NetworkTab").then((m) => ({ default: m.NetworkTab }));
export const loadSavedExportTab: TabLoader = () =>
  import("../tabs/SavedExportTab").then((m) => ({ default: m.SavedExportTab }));
export const loadRoutesIndex: TabLoader = () =>
  import("../tabs/RoutesIndex").then((m) => ({ default: m.RoutesIndex }));
export const loadRouteDossier: TabLoader = () =>
  import("../tabs/RouteDossier").then((m) => ({ default: m.RouteDossier }));
export const loadTimeTab: TabLoader = () => import("../tabs/TimeTab").then((m) => ({ default: m.TimeTab }));
export const loadWhyTab: TabLoader = () => import("../tabs/WhyTab").then((m) => ({ default: m.WhyTab }));
export const loadCompareTab: TabLoader = () =>
  import("../tabs/CompareTab").then((m) => ({ default: m.CompareTab }));

/**
 * Agency-relative route segment → the chunk that segment renders. Keyed by the
 * segment rather than the full path because that is what a nav link knows
 * about its own destination before the router has resolved it.
 */
export const ROUTE_CHUNK_LOADERS: Record<Destination | "ask", () => Promise<unknown>> = {
  pulse: loadOverviewTab,
  // A thin destination only hosts a report screen, which is a chunk of its own.
  routes: () => Promise.all([loadRoutesIndex(), loadAnalysisTab()]),
  time: () => Promise.all([loadTimeTab(), loadAnalysisTab()]),
  why: () => Promise.all([loadWhyTab(), loadAnalysisTab()]),
  // Compare hosts either board: the agencies one renders NetworkTab.
  compare: () => Promise.all([loadCompareTab(), loadAnalysisTab(), loadNetworkTab()]),
  live: loadMapTab,
  // The Reports host lazy-loads its default view, so warm both.
  reports: () => Promise.all([loadSavedExportTab(), loadReportsHomeTab()]),
  ask: loadAskTab,
};

/**
 * Start downloading a tab's chunk before the user commits to going there.
 * Repeat calls are free — the module registry returns the in-flight promise.
 * A failure is swallowed on purpose: this is speculative work, and the real
 * navigation will surface the error through the route's own error boundary.
 */
export function prefetchRouteChunk(segment: string): void {
  if (!Object.hasOwn(ROUTE_CHUNK_LOADERS, segment)) return;
  void ROUTE_CHUNK_LOADERS[segment as Destination | "ask"]().catch(() => {});
}
