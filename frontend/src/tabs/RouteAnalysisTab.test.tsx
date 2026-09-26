import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { RouteAnalysisTab } from "./RouteAnalysisTab";
import type { RouteShapeResponse } from "../api/types";

vi.mock("../components/analysis/AnalysisMap", () => ({
  AnalysisMap: () => <div data-testid="analysis-map" />,
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
    expect(screen.getByRole("heading", { name: "Where does delay build up?" })).toBeInTheDocument();
    expect(screen.getByText("Delay by stop")).toBeInTheDocument();
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
    expect(tab("Delay trend")).toHaveAttribute("aria-selected", "true");
    expect(tab("Map")).toHaveAttribute("aria-selected", "false");
  });

  it("restores the map sub-tab from the URL without a click", async () => {
    renderWithSubTab("&sub_tab=map");
    expect(tab("Map")).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByTestId("analysis-map")).toBeInTheDocument();
  });

  it("names each panel from the tab that controls it", () => {
    renderWithSubTab("");
    const trendTab = tab("Delay trend");
    const panel = screen.getByRole("tabpanel");
    expect(trendTab).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", trendTab.id);
  });

  it("moves selection and focus along the tablist with the arrow keys", () => {
    renderWithSubTab("");
    const trendTab = tab("Delay trend");
    trendTab.focus();
    expect(trendTab).toHaveAttribute("tabindex", "0");
    expect(tab("Time–distance")).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(trendTab, { key: "ArrowRight" });
    expect(tab("Time–distance")).toHaveAttribute("aria-selected", "true");
    expect(tab("Time–distance")).toHaveFocus();

    fireEvent.keyDown(tab("Time–distance"), { key: "ArrowLeft" });
    expect(tab("Delay trend")).toHaveAttribute("aria-selected", "true");
    expect(tab("Delay trend")).toHaveFocus();

    // Wraps rather than dead-ending at the edge, per the tabs pattern.
    fireEvent.keyDown(tab("Delay trend"), { key: "ArrowLeft" });
    expect(tab("By stop")).toHaveFocus();
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
    expect(screen.queryByRole("heading", { name: "Where does delay build up?" })).toBeNull();
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
