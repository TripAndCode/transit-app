import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { ScopeRouteContext } from "../api/scope";
import { RouteAnalysisTab } from "./RouteAnalysisTab";
import { readAnalyses } from "../components/analysis/savedAnalyses";
import type { RouteShapeResponse } from "../api/types";

const mapProps = vi.hoisted(() => ({ last: null as null | { positions?: Array<{ key: string; lon: number; lat: number; delaySec: number }> } }));

vi.mock("../components/analysis/AnalysisMap", () => ({
  AnalysisMap: (props: { positions?: Array<{ key: string; lon: number; lat: number; delaySec: number }> }) => {
    mapProps.last = props;
    return <div data-testid="analysis-map" />;
  },
}));

function mockSupportHooks() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    data: new Map(),
    isLoading: false,
    format: (code: string | null | undefined) => code ?? "—",
  });
}

function shape(stops: RouteShapeResponse["stops"] = []): RouteShapeResponse {
  return { route: "R1", geometry: null, stops };
}

function renderTab(initialPath: string) {
  renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RouteAnalysisTab", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prompts to choose a route and pattern when no single route is selected", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined, isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis");
    expect(screen.getByText("Choose a route and service pattern")).toBeInTheDocument();
  });

  it("shows the empty state when the selected route has no stop observations", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: shape([]), isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByText("No observations match these filters")).toBeInTheDocument();
  });

  it("offers no route-clearing recovery on a route's own page, where the route is the page", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: shape([]), isPending: false, error: null, refetch: vi.fn() } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/routes/R1"]}>
        <Routes>
          <Route
            path="/agencies/:agencyId/routes/:routeCode"
            element={
              <ScopeRouteContext value="R1">
                <RouteAnalysisTab />
              </ScopeRouteContext>
            }
          />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("No observations match these filters")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Clear the route filter" })).toBeNull();
  });

  it("renders the investigate heading and stop-delay content once a route has data", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([
        { stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 },
      ]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByRole("heading", { level: 1, name: "R1" })).toBeInTheDocument();
    expect(screen.getByText("Delay by stop")).toBeInTheDocument();
  });

  it("titles the page by its route, asks its question beneath, and leads back to Routes", () => {
    mockSupportHooks();
    vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
      data: new Map([["R1", "W54 沖舘・新田線 · for 新田"]]),
      isLoading: false,
      format: (code: string | null | undefined) => (code === "R1" ? "W54 沖舘・新田線 · for 新田" : (code ?? "—")),
    });
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([{ stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByRole("heading", { level: 1, name: "W54 沖舘・新田線 · for 新田" })).toBeInTheDocument();
    expect(screen.getByText("Where does delay build up?")).toBeInTheDocument();
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Routes" })).toHaveAttribute("href", expect.stringMatching(/^\/agencies\/1\/routes/));
    expect(crumbs).toHaveTextContent("W54 沖舘・新田線 · for 新田");
  });

  it("titles a saved analysis with its scope in words", () => {
    localStorage.clear();
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([{ stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1&from=2026-09-01&to=2026-09-28");
    fireEvent.click(screen.getByRole("button", { name: "Save analysis" }));
    expect(readAnalyses()[0].title).toBe("R1, 9/1 – 9/28, every day, all day, within 1 min");
  });
});

function renderRecoveryTab(
  path: string,
  response: RouteShapeResponse | undefined = { route: "A05", geometry: null, stops: [] } as RouteShapeResponse,
) {
  vi.spyOn(hooks, "useRouteShape").mockReturnValue({
    data: response,
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
    isPending: false,
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("RouteAnalysisTab empty state recoveries", () => {
  it("offers a jump-to-latest-data recovery when a route is scoped but its window has no stop data", () => {
    renderRecoveryTab("/agencies/8/route-analysis?from=2020-01-01&to=2020-01-07&routes=A05");
    expect(screen.getByRole("button", { name: "Jump to the latest data" })).toBeInTheDocument();
  });

  it("offers a reset-service recovery when service is scoped to a non-default value", () => {
    renderRecoveryTab("/agencies/8/route-analysis?from=2020-01-01&to=2020-01-07&routes=A05&service=%E5%B9%B3%E6%97%A5");
    expect(screen.getByRole("button", { name: "Reset service type to all" })).toBeInTheDocument();
  });
});

const STOP = { stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 };

function renderWithSubTab(search: string) {
  mockSupportHooks();
  vi.spyOn(hooks, "useRouteShape").mockReturnValue({
    data: shape([STOP]),
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
  renderTab(`/agencies/1/route-analysis?routes=R1${search}`);
}

function tab(name: string) {
  return screen.getByRole("tab", { name });
}

describe("RouteAnalysisTab sub-tab state", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("falls back to the trend sub-tab when the URL names one that does not exist", () => {
    renderWithSubTab("&sub_tab=garbage");
    expect(tab("Along the route")).toHaveAttribute("aria-selected", "true");
    expect(tab("Map")).toHaveAttribute("aria-selected", "false");
  });

  it("restores the map sub-tab from the URL without a click", async () => {
    renderWithSubTab("&sub_tab=map");
    expect(tab("Map")).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
  });

  it("names each panel from the tab that controls it", () => {
    renderWithSubTab("");
    const trendTab = tab("Along the route");
    const panel = screen.getByRole("tabpanel");
    expect(trendTab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", trendTab.id);
  });

  it("moves selection and focus along the tablist with the arrow keys", () => {
    renderWithSubTab("");
    const trendTab = tab("Along the route");
    trendTab.focus();
    expect(trendTab).toHaveAttribute("tabindex", "0");
    expect(tab("Trips over time")).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(trendTab, { key: "ArrowRight" });
    expect(tab("Trips over time")).toHaveAttribute("aria-selected", "true");
    expect(tab("Trips over time")).toHaveFocus();

    fireEvent.keyDown(tab("Trips over time"), { key: "ArrowLeft" });
    expect(tab("Along the route")).toHaveAttribute("aria-selected", "true");
    expect(tab("Along the route")).toHaveFocus();

    // Wraps rather than dead-ending at the edge, per the tabs pattern.
    fireEvent.keyDown(tab("Along the route"), { key: "ArrowLeft" });
    expect(tab("Stop table")).toHaveFocus();
  });
});

describe("RouteAnalysisTab without a usable agency id", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders nothing when the route segment is not an agency id", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([STOP]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/not-an-id/route-analysis?routes=R1");
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
  });
});

describe("RouteAnalysisTab map chunk", () => {
  it("loads AnalysisMap dynamically so hovering this tab does not prefetch MapLibre", () => {
    const source = readFileSync(resolve(process.cwd(), "src/tabs/RouteAnalysisTab.tsx"), "utf8");
    // A static import would pull maplibre-gl into the route-analysis chunk,
    // which the sidebar warms on hover.
    expect(source).not.toMatch(/^import \{[^}]*AnalysisMap/m);
    expect(source).toMatch(/import\("\.\.\/components\/analysis\/AnalysisMap"\)/);
  });
});

describe("RouteAnalysisTab Marey scrubber", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderScrubTab(search: string) {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([
        { stop_sequence: 1, stop_name: "A", lon: 132, lat: 34, avg_min: 1, samples: 5 },
        { stop_sequence: 2, stop_name: "B", lon: 134, lat: 36, avg_min: 2, samples: 5 },
      ]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useRouteTrips").mockReturnValue({
      data: {
        date: "2026-10-01",
        time_band: "all",
        truncated: false,
        trips: [
          {
            trip_id: "T1",
            scheduled_time: "07:00",
            headsign: null,
            avg_delay_sec: 0,
            samples: 2,
            stops: [
              { stop_id: "a", stop_sequence: 1, scheduled_sec: 25_200, observed_sec: 25_200, delay_sec: 0 },
              { stop_id: "b", stop_sequence: 2, scheduled_sec: 25_800, observed_sec: 25_800, delay_sec: 0 },
            ],
          },
        ],
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab(`/agencies/1/route-analysis?routes=R1${search}`);
  }

  it("scrubbing changes the readout and feeds one position to the map", async () => {
    renderScrubTab("&sub_tab=marey");
    fireEvent.change(screen.getByRole("slider", { name: "Scrub the clock" }), { target: { value: "25500" } });
    expect(screen.getByTestId("marey-readout")).toHaveTextContent("1 trips under way");

    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
    expect(mapProps.last?.positions).toHaveLength(1);
    expect(mapProps.last?.positions?.[0]).toMatchObject({ key: "T1", lon: 133, lat: 35 });
  });
});
