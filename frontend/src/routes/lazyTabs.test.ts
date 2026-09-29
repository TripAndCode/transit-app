import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi } from "vitest";
import { ROUTE_CHUNK_LOADERS, loadMapTab, loadSavedExportTab, prefetchRouteChunk } from "./lazyTabs";
import { SIDEBAR_NAV_ITEMS } from "../components/sidebarNavItems";

vi.mock("../tabs/AnalysisWorkspace", () => ({ AnalysisWorkspace: () => null }));
vi.mock("../tabs/OverviewTab", () => ({ OverviewTab: () => null }));

const mainTsx = readFileSync(resolve(process.cwd(), "src/main.tsx"), "utf8");
const appTsx = readFileSync(resolve(process.cwd(), "src/App.tsx"), "utf8");

describe("ROUTE_CHUNK_LOADERS", () => {
  it("covers every destination the sidebar can navigate to", () => {
    for (const item of SIDEBAR_NAV_ITEMS) {
      expect(ROUTE_CHUNK_LOADERS[item.to]).toBeTypeOf("function");
    }
    expect(ROUTE_CHUNK_LOADERS.ask).toBeTypeOf("function");
  });

  it("maps each destination to the chunk it renders, and nothing else", () => {
    expect(Object.keys(ROUTE_CHUNK_LOADERS).sort()).toEqual(["analysis", "ask", "live", "saved"]);
    expect(ROUTE_CHUNK_LOADERS.live).toBe(loadMapTab);
    expect(ROUTE_CHUNK_LOADERS.saved).toBe(loadSavedExportTab);
  });

  it("warms the Overview lens with the workspace, since Analysis lands there", async () => {
    const { AnalysisWorkspace } = await import("../tabs/AnalysisWorkspace");
    const { OverviewTab } = await import("../tabs/OverviewTab");
    const loaded = await ROUTE_CHUNK_LOADERS.analysis();
    expect(loaded).toEqual([{ default: AnalysisWorkspace }, { default: OverviewTab }]);
  });

  it("is the only place the routed tabs are dynamically imported", () => {
    // The prefetch and the route's own lazy() must go through the same
    // import expression, or the browser fetches two different chunks.
    expect(mainTsx).not.toMatch(/import\("\.\/tabs\//);
  });
});

describe("prefetchRouteChunk", () => {
  it("invokes the loader for a known route segment", () => {
    const loader = vi.spyOn(ROUTE_CHUNK_LOADERS, "saved").mockResolvedValue({ default: () => null });
    prefetchRouteChunk("saved");
    expect(loader).toHaveBeenCalledTimes(1);
    loader.mockRestore();
  });

  it("is a no-op for a segment with no chunk of its own", () => {
    expect(() => prefetchRouteChunk("not-a-route")).not.toThrow();
  });

  it("swallows a failed chunk fetch — a prefetch must never surface an error", async () => {
    const loader = vi.spyOn(ROUTE_CHUNK_LOADERS, "saved").mockRejectedValue(new Error("offline"));
    expect(() => prefetchRouteChunk("saved")).not.toThrow();
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
