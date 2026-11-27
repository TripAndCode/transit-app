import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import * as useRouteNamesModule from "../api/useRouteNames";
import { MapTab } from "./MapTab";
import { stubReducedMotion } from "../test/reducedMotion";
import { WINDOW_DELAYS, liveRowsWithDelays } from "../test/liveTripFixtures";

vi.mock("maplibre-gl", () => import("../test/maplibreMock"));

// Live's window is the shared default's, whatever that default is: with the
// file naming a different preset, the rate and its note must follow it rather
// than a window of Live's own.
vi.mock("../api/onTimeDefault.json", async (importOriginal) => {
  const actual = await importOriginal<{ default: { presets: Record<string, unknown> } }>();
  return { default: { ...actual.default, default_preset: "legacy_60s" } };
});

describe("MapTab's on-time rate under another default", () => {
  beforeEach(() => {
    stubReducedMotion();
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: null, date: null, routes: [], raw_samples: 0, clamp_count: 0 },
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
    vi.spyOn(hooks, "useServiceHours").mockReturnValue({ data: { date: "2026-01-01", hours: null }, isLoading: false, error: null } as never);
    vi.spyOn(hooks, "useRouteShape").mockReturnValue({ data: undefined } as never);
    vi.spyOn(hooks, "useLiveTripProgress").mockReturnValue({ data: undefined, isLoading: false } as never);
    vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({
      ...useRouteNamesModule.buildRouteNames([], i18n.t),
      format: (code: string | null | undefined) => code ?? "—",
    });
    vi.spyOn(hooks, "useLiveTrips").mockReturnValue({
      dataUpdatedAt: 0,
      data: { latest_captured_at: "2026-06-01T00:00:00Z", rows: liveRowsWithDelays(WINDOW_DELAYS), truncated: false },
      error: null,
      isLoading: false,
      isFetching: false,
      refetch: vi.fn(),
    } as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("counts and names the default's window", () => {
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/map"]}>
        <Routes>
          <Route path="/agencies/:agencyId/map" element={<MapTab />} />
        </Routes>
      </MemoryRouter>,
    );
    const tile = screen.getByText("On-time rate").closest(".stat-tile");
    expect(tile?.querySelector(".stat-tile__value")).toHaveTextContent("43%");
    expect(screen.getByTestId("live-on-time-note")).toHaveTextContent("within 1 min late, any early departure on time");
  });
});
