import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { ScopeRouteContext } from "../api/scope";
import { RouteAnalysisTab } from "./RouteAnalysisTab";
import { readAnalyses } from "../components/analysis/savedAnalyses";
import type { RouteShapeResponse } from "../api/types";

const mapProps = vi.hoisted(() => ({ last: null as null | { positions?: Array<{ key: string; lon: number; lat: number; delaySec: number }> } }));

vi.mock("../components/RouteTitleTransition", () => ({
  RouteTitleTransition: ({ children }: { children: React.ReactNode }) => <div data-testid="route-title-transition">{children}</div>,
}));

vi.mock("../components/analysis/AnalysisMap", () => ({
  AnalysisMap: (props: { positions?: Array<{ key: string; lon: number; lat: number; delaySec: number }> }) => {
    mapProps.last = props;
    return <div data-testid="analysis-map" />;
  },
}));

function mockSupportHooks() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    ...useRouteNamesModule.buildRouteNames([], i18n.t),
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

  it("the dossier title is where a clicked route label travels to, and only while a route is on the page", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: shape([]), isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    expect(screen.getByTestId("route-title-transition")).toContainElement(screen.getByRole("heading", { level: 1 }));
  });

  it("the choose-a-route heading is not a travel target", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined, isPending: false, error: null, refetch: vi.fn() } as never);
    renderTab("/agencies/1/route-analysis");
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.queryByTestId("route-title-transition")).toBeNull();
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
    vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue(
      useRouteNamesModule.buildRouteNames(
        [{ route_id: "沖舘・新田線(R1)", route_short_name: "W54", route_long_name: "沖舘・新田線", route_code: "R1", trip_headsigns: [], origin_name: "沖舘", destination_name: "新田" }],
        i18n.t,
      ),
    );
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([{ stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    const title = screen.getByRole("heading", { level: 1 });
    expect(title).toHaveTextContent(/^W54 沖舘・新田線 沖舘 → 新田$/);
    expect(title.querySelector(".route-label__plate")).toHaveTextContent("W54");
    expect(screen.getByText("Where does delay build up?", { exact: false })).toBeInTheDocument();
    // Lists drop the route_id; the route's own page keeps it, below the
    // figures rather than in the subtitle.
    expect(screen.getByText("Route ID 沖舘・新田線(R1)")).toBeInTheDocument();
    expect(screen.getByText("Where does delay build up?")).not.toHaveTextContent("Route ID");
    const crumbs = screen.getByRole("navigation", { name: "Breadcrumb" });
    expect(within(crumbs).getByRole("link", { name: "Routes" })).toHaveAttribute("href", expect.stringMatching(/^\/agencies\/1\/routes/));
    expect(crumbs).toHaveTextContent("W54 沖舘・新田線");
  });

  it("writes the chart's periods the way the rest of the page writes dates", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([{ stop_sequence: 1, stop_name: "Stop A", lon: 140.7, lat: 40.8, avg_min: 2.4, samples: 10 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1&from=2026-08-12&to=2026-09-10&compare=1");
    const periods = screen.getByText(/^Selected period /);
    expect(periods).toHaveTextContent("Selected period Aug 12 – Sep 10, 2026");
    // The 30 days just before, sharing no day with the selected ones.
    expect(periods).toHaveTextContent("Previous 30 days Jul 13 – Aug 11, 2026");
    expect(periods).not.toHaveTextContent("2026-08-12");
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
    expect(readAnalyses()[0].title).toBe("R1, 9/1 – 9/28, every day, all hours, within 1 min");
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

  it("prints each stop's mean in the stop table at one decimal, as the selected stop's figure is", () => {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: shape([{ ...STOP, avg_min: 2 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1");
    fireEvent.click(tab("Stop table"));
    const table = screen.getByRole("table");
    expect(within(table).getByText("2.0")).toBeInTheDocument();
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

  const TRIP = (id: string, from: number, to: number) => ({
    trip_id: id,
    scheduled_time: null,
    headsign: null,
    avg_delay_sec: 0,
    samples: 2,
    stops: [
      { stop_id: "a", stop_sequence: 1, scheduled_sec: from, observed_sec: from, delay_sec: 0 },
      { stop_id: "b", stop_sequence: 2, scheduled_sec: to, observed_sec: to, delay_sec: 0 },
    ],
  });

  function renderTrips(trips: ReturnType<typeof TRIP>[], data: RouteShapeResponse) {
    mockSupportHooks();
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useRouteTrips").mockReturnValue({
      data: { date: "2026-10-01", time_band: "all", truncated: false, trips },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab("/agencies/1/route-analysis?routes=R1&sub_tab=marey");
  }

  it("the map shows only the trips the diagram draws: one that left before the window stays off both", async () => {
    // Departs 05:45, still running at 06:05; the default window opens at 06:00.
    renderTrips([TRIP("EARLY", 20_700, 22_200), TRIP("T1", 25_200, 25_800)], shape([
      { stop_sequence: 1, stop_name: "A", lon: 132, lat: 34, avg_min: 1, samples: 5 },
      { stop_sequence: 2, stop_name: "B", lon: 134, lat: 36, avg_min: 2, samples: 5 },
    ]));
    fireEvent.change(screen.getByRole("slider", { name: "Scrub the clock" }), { target: { value: "21900" } });
    expect(screen.getByTestId("marey-readout")).toHaveTextContent("0 trips under way");
    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
    expect(mapProps.last?.positions).toEqual([]);
  });

  it("feeds the map only while it is the tab on screen, not on every scrub step behind it", async () => {
    renderTrips([TRIP("T1", 25_200, 25_800)], shape([
      { stop_sequence: 1, stop_name: "A", lon: 132, lat: 34, avg_min: 1, samples: 5 },
      { stop_sequence: 2, stop_name: "B", lon: 134, lat: 36, avg_min: 2, samples: 5 },
    ]));
    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Trips over time" }));
    fireEvent.change(screen.getByRole("slider", { name: "Scrub the clock" }), { target: { value: "25500" } });
    expect(mapProps.last?.positions).toEqual([]);
    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(mapProps.last?.positions).toHaveLength(1);
  });

  it("places a trip on the map on a leg that runs to a stop unobserved in the range", async () => {
    renderTrips([TRIP("T1", 25_200, 25_800)], {
      ...shape([{ stop_sequence: 1, stop_name: "A", lon: 132, lat: 34, avg_min: 1, samples: 5 }]),
      unobserved_stops: [{ stop_sequence: 2, stop_name: "B", lon: 134, lat: 36 }],
    });
    fireEvent.change(screen.getByRole("slider", { name: "Scrub the clock" }), { target: { value: "25500" } });
    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
    expect(mapProps.last?.positions?.[0]).toMatchObject({ key: "T1", lon: 133, lat: 35 });
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
    expect(screen.getByTestId("marey-readout")).toHaveTextContent("1 trip under way");

    fireEvent.click(screen.getByRole("tab", { name: "Map" }));
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
    expect(mapProps.last?.positions).toHaveLength(1);
    expect(mapProps.last?.positions?.[0]).toMatchObject({ key: "T1", lon: 133, lat: 35 });
  });
});

describe("RouteAnalysisTab where delay grows", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  const STOPS = [
    { stop_sequence: 1, stop_name: "広島駅", stop_id: "a", lon: 132.1, lat: 34.1, avg_min: 0, samples: 50 },
    { stop_sequence: 2, stop_name: "大手町四丁目", stop_id: "b", lon: 132.2, lat: 34.2, avg_min: 2.7, samples: 50 },
    { stop_sequence: 3, stop_name: "加古町", stop_id: "c", lon: 132.3, lat: 34.3, avg_min: 3.9, samples: 50 },
    { stop_sequence: 4, stop_name: "観音本町", stop_id: "d", lon: 132.4, lat: 34.4, avg_min: 4.4, samples: 50 },
  ];
  const VARIANTS = [
    { shape_id: "out", first_stop_name: "広島駅", last_stop_name: "観音新町三丁目", stop_count: 5, samples: 300, share_pct: 31.8 },
    { shape_id: "in", first_stop_name: "観音新町三丁目", last_stop_name: "広島駅", stop_count: 5, samples: 250, share_pct: 25 },
  ];

  function renderDossier(search = "") {
    mockSupportHooks();
    const useRouteShape = vi.spyOn(hooks, "useRouteShape").mockReturnValue({
      data: {
        ...shape(STOPS),
        shape_id: "out",
        variants: VARIANTS,
        segments: [
          { from_sequence: 2, to_sequence: 3, increase_min: 1.2, pairs: 50 },
          { from_sequence: 3, to_sequence: 4, increase_min: 0.47, pairs: 50 },
        ],
        unobserved_stops: [{ stop_sequence: 5, stop_name: "観音新町三丁目", lon: 132.5, lat: 34.5 }],
      },
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderTab(`/agencies/1/route-analysis?routes=R1${search}`);
    return useRouteShape;
  }

  it("says where delay grows most and marks it on the chart", () => {
    renderDossier();
    expect(screen.getByText("Delay grows most from 大手町四丁目 to 加古町 (+1.2 min)")).toBeInTheDocument();
    expect(screen.getByText("Next largest: 加古町 to 観音本町 (+0.5 min).")).toBeInTheDocument();
    expect(screen.getByTestId("stop-chart-highlight")).toBeInTheDocument();
  });

  it("opens on the stop with the largest delay, not the first stop at 0", () => {
    renderDossier();
    expect(screen.getByRole("combobox", { name: "Selected stop" })).toHaveDisplayValue("観音本町");
  });

  it("calls the last stop the last stop, not unobserved, since nothing departs it", () => {
    renderDossier();
    fireEvent.click(tab("Stop table"));
    const table = screen.getByRole("table");
    expect(within(table).getByText("Last stop (no departures)")).toBeInTheDocument();
    expect(within(table).queryByText("No observation")).toBeNull();
  });

  it("lists the route's paths with their shares and switches to the other direction", () => {
    const useRouteShape = renderDossier("&stop_route=R1&stop_seq=3");
    const picker = screen.getByRole("combobox", { name: "Path" });
    expect(picker).toHaveDisplayValue("広島駅 → 観音新町三丁目 (5 stops, 32% of observations)");
    expect(screen.getByText("This path: 32% of the route's observations")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Selected stop" })).toHaveDisplayValue("加古町");
    fireEvent.click(screen.getByRole("button", { name: "Other direction" }));
    const asked = useRouteShape.mock.calls.filter((call) => call[1] === "R1").at(-1);
    expect(asked?.[3]).toBe("in");
    // The other path numbers its stops afresh, so the picked stop is dropped.
    expect(screen.getByRole("combobox", { name: "Selected stop" })).toHaveDisplayValue("観音本町");
  });

  it("compares with the previous window on the variant shown", () => {
    const useRouteShape = renderDossier("&from=2026-09-24&to=2026-10-23&compare=1");
    const previousCall = useRouteShape.mock.calls.find((call) => call[2].from === "2026-08-25");
    expect(previousCall?.[2]).toMatchObject({ from: "2026-08-25", to: "2026-09-23" });
    expect(previousCall?.[3]).toBe("out");
  });
});
