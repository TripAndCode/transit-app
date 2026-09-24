import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { MapTab } from "./MapTab";
import type { LiveTripsResponse, RouteSummaryResponse } from "../api/types";

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
