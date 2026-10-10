import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { ROUTE_CHUNK_LOADERS, loadMapTab, loadOverviewTab, prefetchRouteChunk } from "./lazyTabs";
import { DESTINATIONS } from "./destinations";
import type { Destination } from "./destinations";

vi.mock("../tabs/RoutesIndex", () => ({ RoutesIndex: () => null }));
vi.mock("../tabs/AnalysisTab", () => ({ AnalysisTab: () => null }));
vi.mock("../tabs/SavedExportTab", () => ({ SavedExportTab: () => null }));
vi.mock("../tabs/ReportsHomeTab", () => ({ ReportsHomeTab: () => null }));
vi.mock("../tabs/CompareTab", () => ({ CompareTab: () => null }));
vi.mock("../tabs/NetworkTab", () => ({ NetworkTab: () => null }));

const mainTsx = readFileSync(resolve(process.cwd(), "src/main.tsx"), "utf8");
const appTsx = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

describe("ROUTE_CHUNK_LOADERS", () => {
  it("covers every destination the rail can navigate to", () => {
    for (const dest of DESTINATIONS) {
      expect(ROUTE_CHUNK_LOADERS[dest]).toBeTypeOf("function");
    }
    expect(ROUTE_CHUNK_LOADERS.ask).toBeTypeOf("function");
  });

  it("is keyed by destination at compile time, so a new destination needs a loader", () => {
    const _everyDestinationHasALoader: Record<Destination | "ask", unknown> = ROUTE_CHUNK_LOADERS;
    void _everyDestinationHasALoader;
    // A string-keyed map would satisfy the line above too; pin the key type
    // itself so the loaders cannot drift back to accepting any segment.
    const _keyedByDestination: [keyof typeof ROUTE_CHUNK_LOADERS] extends [Destination | "ask"] ? true : false = true;
    void _keyedByDestination;
  });

  it("maps each destination to the chunk it renders, and nothing else", () => {
    expect(Object.keys(ROUTE_CHUNK_LOADERS).sort()).toEqual([
      "ask",
      "compare",
      "live",
      "pulse",
      "reports",
      "routes",
      "time",
      "why",
    ]);
    expect(ROUTE_CHUNK_LOADERS.pulse).toBe(loadOverviewTab);
    expect(ROUTE_CHUNK_LOADERS.live).toBe(loadMapTab);
  });

  it("warms the report screen a thin destination hosts along with it", async () => {
    const { RoutesIndex } = await import("../tabs/RoutesIndex");
    const { AnalysisTab } = await import("../tabs/AnalysisTab");
    const loaded = await ROUTE_CHUNK_LOADERS.routes();
    expect(loaded).toEqual([{ default: RoutesIndex }, { default: AnalysisTab }]);
  });

  it("warms the default Reports view along with the Reports host", async () => {
    const { SavedExportTab } = await import("../tabs/SavedExportTab");
    const { ReportsHomeTab } = await import("../tabs/ReportsHomeTab");
    const loaded = await ROUTE_CHUNK_LOADERS.reports();
    expect(loaded).toEqual([{ default: SavedExportTab }, { default: ReportsHomeTab }]);
  });

  it("warms the agencies board and the analysis board along with the Compare host", async () => {
    const { CompareTab } = await import("../tabs/CompareTab");
    const { AnalysisTab } = await import("../tabs/AnalysisTab");
    const { NetworkTab } = await import("../tabs/NetworkTab");
    const loaded = await ROUTE_CHUNK_LOADERS.compare();
    expect(loaded).toEqual([{ default: CompareTab }, { default: AnalysisTab }, { default: NetworkTab }]);
  });

  it("is the only place the routed tabs are dynamically imported", () => {
    // The prefetch and the route's own lazy() must go through the same
    // import expression, or the browser fetches two different chunks.
    expect(mainTsx).not.toMatch(/import\("\.\/tabs\//);
  });
});

describe("prefetchRouteChunk", () => {
  it("invokes the loader for a known route segment", () => {
    const loader = vi.spyOn(ROUTE_CHUNK_LOADERS, "reports").mockResolvedValue({ default: () => null });
    prefetchRouteChunk("reports");
    expect(loader).toHaveBeenCalledTimes(1);
    loader.mockRestore();
  });

  it("is a no-op for a segment with no chunk of its own", () => {
    expect(() => prefetchRouteChunk("not-a-route")).not.toThrow();
  });

  it("is a no-op for a segment that names an inherited object property", () => {
    expect(() => prefetchRouteChunk("toString")).not.toThrow();
    expect(() => prefetchRouteChunk("constructor")).not.toThrow();
  });

  it("swallows a failed chunk fetch — a prefetch must never surface an error", async () => {
    const loader = vi.spyOn(ROUTE_CHUNK_LOADERS, "reports").mockRejectedValue(new Error("offline"));
    expect(() => prefetchRouteChunk("reports")).not.toThrow();
    await Promise.resolve();
    loader.mockRestore();
  });
});

describe("router shell", () => {
  it("opts the data router into React's startTransition for navigations", () => {
    expect(mainTsx).toMatch(/future=\{\{\s*v7_startTransition:\s*true\s*\}\}/);
  });

  it("keeps one Suspense boundary above the Outlet instead of one per tab", () => {
    // A boundary created fresh per route would mount empty and show its
    // fallback, so the outgoing tab could never stay painted across a
    // transition. The shell owns the boundary; the tab routes do not.
    expect(appTsx).toMatch(/<Suspense/);
    expect(mainTsx).not.toMatch(/el\(<(Overview|Map|Ask|Analysis|RouteAnalysis|ReportsHome|Network)Tab/);
  });
});
