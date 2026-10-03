import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useNavigate } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { MapTab } from "./MapTab";
import { stubReducedMotion } from "../test/reducedMotion";
import type { LiveTrip, LiveTripsResponse, RouteSummaryResponse } from "../api/types";
import { MockMap } from "../test/maplibreMock";

vi.mock("maplibre-gl", () => import("../test/maplibreMock"));

function liveTrips(rows: LiveTripsResponse["rows"] = []): LiveTripsResponse {
  return { latest_captured_at: rows.length ? "2026-06-01T00:00:00Z" : null, rows };
}

function todaySummary(): RouteSummaryResponse {
  return { latest_captured_at: null, date: null, routes: [], raw_samples: 0, clamp_count: 0 };
}

function mockCommonHooks() {
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
    data: todaySummary(),
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  } as never);
  vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined } as never);
  vi.spyOn(hooks, "useLiveTripProgress").mockReturnValue({ data: undefined, isLoading: false } as never);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
    data: new Map(),
    isLoading: false,
    format: (code: string | null | undefined) => code ?? "—",
  });
}

function renderMap(agencyId = "1", search = "") {
  renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/map${search}`]}>
      <Routes>
        <Route path="/agencies/:agencyId/map" element={<MapTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MapTab", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the operations heading and the empty state when there are no live trips", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.getByRole("heading", { name: "Current observations" })).toBeInTheDocument();
    expect(screen.getByText("No current trips to display")).toBeInTheDocument();
  });

  it("does not show the empty state and counts observed trips once live rows arrive", () => {
    mockCommonHooks();
    const capturedAt = new Date().toISOString();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      data: liveTrips([
        {
          trip_id: "t1",
          route_code: "R1",
          service_type: null,
          scheduled_time: "08:00:00",
          dep_delay: 30,
          captured_at: capturedAt,
          stop_id: "s1",
          stop_sequence: 1,
          stop_name: "Stop 1",
          stop_lat: 40.8,
          stop_lon: 140.7,
          headsign: "Downtown",
        },
      ]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap();
    expect(screen.queryByText("No current trips to display")).not.toBeInTheDocument();
    expect(screen.getByText("1", { exact: true })).toBeInTheDocument();
  });
});

describe("MapTab basemap style from the URL", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function renderWithStyle(search: string) {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap("1", search);
    const entry = screen.getByRole("button", { name: "Map style" });
    return entry.querySelector("img")?.getAttribute("src") ?? "";
  }

  it("honours a style id the catalog defines", () => {
    expect(renderWithStyle("?style=photo")).toContain("/seamlessphoto/");
  });

  it("falls back to the default basemap for a style id the catalog does not define", () => {
    const src = renderWithStyle("?style=garbage");
    expect(src).toContain("/pale/");
    expect(src).not.toContain("tile.openstreetmap.org");
  });
});

describe("MapTab delayed-trips cap", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  function delayedTrip(route: string, index: number, capturedAt: string): LiveTrip {
    return {
      trip_id: `${route}-${index}`,
      route_code: route,
      service_type: null,
      scheduled_time: "08:00:00",
      dep_delay: 600 + index,
      captured_at: capturedAt,
      stop_id: `s${index}`,
      stop_sequence: 1,
      stop_name: `Stop ${index}`,
      stop_lat: 40.8,
      stop_lon: 140.7,
      headsign: "Downtown",
    };
  }

  /** Stands in for the filter dock: a route-filter change is a URL change. */
  function FilterRoutes() {
    const navigate = useNavigate();
    return <button type="button" onClick={() => navigate("/agencies/1/map?routes=R1,R3")}>refilter</button>;
  }

  it("keeps a raised cap across a live refetch and resets it when the route filter changes", () => {
    mockCommonHooks();
    vi.spyOn(hooks, "useRouteStopProfile").mockReturnValue({ data: undefined } as never);
    const capturedAt = new Date().toISOString();
    // Either two-route filter holds just past the 200-row cap. Two routes
    // rather than one keeps any single route from being focused, which would
    // render its every trip a second time in the trip panel.
    const rows = [
      ...Array.from({ length: 201 }, (_, i) => delayedTrip("R1", i, capturedAt)),
      delayedTrip("R2", 0, capturedAt),
      delayedTrip("R3", 0, capturedAt),
    ];
    let liveResult = { data: liveTrips(rows), dataUpdatedAt: 1 };
    vi.spyOn(hooks, "useLiveTrips").mockImplementation(() => ({
      ...liveResult,
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    }) as never);
    // A fresh element per call: re-rendering with the same element object
    // lets React bail out before MapTab ever sees the refetched result.
    const tree = () => (
      <MemoryRouter initialEntries={["/agencies/1/map?routes=R1,R2"]}>
        <FilterRoutes />
        <Routes>
          <Route path="/agencies/:agencyId/map" element={<MapTab />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = renderWithProviders(tree());

    // Text queries, not role queries: resolving accessible names walks every
    // rendered row on each call.
    const remainder = "Show 2 more";
    fireEvent.click(screen.getByText(remainder));
    expect(screen.queryByText(remainder)).not.toBeInTheDocument();

    liveResult = { data: liveTrips(rows), dataUpdatedAt: 2 };
    rerender(tree());
    expect(screen.queryByText(remainder)).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("refilter"));
    expect(screen.getByText(remainder)).toBeInTheDocument();
  });
});

describe("MapTab relief layer", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  /** Reduced motion always (so camera moves are instant cuts); `phone`
   *  additionally matches the mobile breakpoint query. */
  function stubViewport(phone: boolean) {
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: query.includes("prefers-reduced-motion") || (phone && query.includes("max-width")),
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList);
  }

  function renderAndOpenPanel(search = "") {
    mockCommonHooks();
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      data: liveTrips([]),
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    renderMap("1", search);
    fireEvent.click(screen.getByRole("button", { name: "Map style" }));
    return screen.getByRole("button", { name: /Relief/ });
  }

  function pitches(easeTo: { mock: { calls: unknown[][] } }) {
    return easeTo.mock.calls.map(([options]) => (options as { pitch?: number }).pitch);
  }

  it("starts on at desktop width, tilts through the camera module, and persists a switch-off", () => {
    stubViewport(false);
    const easeTo = vi.spyOn(MockMap.prototype, "easeTo");
    const chip = renderAndOpenPanel();
    expect(chip).toHaveAttribute("aria-pressed", "true");
    expect(pitches(easeTo)).toEqual([35]);

    fireEvent.click(chip);
    expect(screen.getByRole("button", { name: /Relief/ })).toHaveAttribute("aria-pressed", "false");
    expect(localStorage.getItem("transit.mapRelief")).toBe("0");
    expect(pitches(easeTo)).toEqual([35, 0]);
  });

  it("starts off on a phone and never tilts the map unasked", () => {
    stubViewport(true);
    const easeTo = vi.spyOn(MockMap.prototype, "easeTo");
    const chip = renderAndOpenPanel();
    expect(chip).toHaveAttribute("aria-pressed", "false");
    expect(easeTo).not.toHaveBeenCalled();
  });

  it("lets the URL override the stored preference", () => {
    stubViewport(false);
    localStorage.setItem("transit.mapRelief", "1");
    expect(renderAndOpenPanel("?relief=0")).toHaveAttribute("aria-pressed", "false");
  });
});
