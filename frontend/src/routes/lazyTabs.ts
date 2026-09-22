import type { ComponentType } from "react";

/**
 * The one place the routed tabs are dynamically imported.
 *
 * `main.tsx` builds its `React.lazy` routes from these loaders and the sidebar
 * prefetches through the same ones on hover/focus. That shared identity is the
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
export const loadAnalysisTab: TabLoader = () =>
  import("../tabs/AnalysisTab").then((m) => ({ default: m.AnalysisTab }));
export const loadRouteAnalysisTab: TabLoader = () =>
  import("../tabs/RouteAnalysisTab").then((m) => ({ default: m.RouteAnalysisTab }));
export const loadReportsHomeTab: TabLoader = () =>
  import("../tabs/ReportsHomeTab").then((m) => ({ default: m.ReportsHomeTab }));
export const loadNetworkTab: TabLoader = () =>
  import("../tabs/NetworkTab").then((m) => ({ default: m.NetworkTab }));

/**
 * Agency-relative route segment → the chunk that segment renders. Keyed by the
 * segment rather than the full path because that is what a nav link knows
 * about its own destination before the router has resolved it.
 */
export const ROUTE_CHUNK_LOADERS: Record<string, TabLoader> = {
  operations: loadMapTab,
  "period-overview": loadOverviewTab,
  // The pre-rename URLs still resolve, via redirects to /operations, so a
  // hover on a stale bookmark warms the chunk it will actually land on.
  overview: loadMapTab,
  map: loadMapTab,
  ask: loadAskTab,
  analysis: loadAnalysisTab,
  "route-analysis": loadRouteAnalysisTab,
  network: loadNetworkTab,
  reports: loadReportsHomeTab,
};

/**
 * Start downloading a tab's chunk before the user commits to going there.
 * Repeat calls are free — the module registry returns the in-flight promise.
 * A failure is swallowed on purpose: this is speculative work, and the real
 * navigation will surface the error through the route's own error boundary.
 */
export function prefetchRouteChunk(segment: string): void {
  void ROUTE_CHUNK_LOADERS[segment]?.().catch(() => {});
}
