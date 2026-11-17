import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import * as slowZonesApi from "./slowZonesApi";
import type { SlowZoneSegmentData, SlowZonesResponse } from "./slowZonesApi";
import { SlowZonesView } from "./SlowZonesView";

type Handler = (event?: unknown) => void;
const maps: Array<{ handlers: Record<string, Handler[]> }> = [];

vi.mock("maplibre-gl", async () => {
  const mock = await import("../../test/maplibreMock");
  class CapturedMap extends mock.MockMap {
    constructor(options: Record<string, unknown>) {
      super(options);
      maps.push(this as unknown as { handlers: Record<string, Handler[]> });
    }
  }
  return { ...mock, Map: CapturedMap };
});

// A full-tier agency, whose not-shown reason is the feed's own.
vi.mock("../../api/useCapabilityTier", () => ({ useCapabilityTier: () => "full" }));

const slow: SlowZoneSegmentData = {
  from_stop_id: "S1",
  from_stop_name: "紙屋町",
  from_lon: 132.455,
  from_lat: 34.395,
  to_stop_id: "S2",
  to_stop_name: "八丁堀",
  to_lon: 132.46,
  to_lat: 34.395,
  distance_m: 459,
  routes: ["R1", "R2"],
  latest: {
    samples: 21,
    days: 1,
    run_p50_sec: 200,
    run_p85_sec: 230,
    scheduled_run_sec: 120,
    excess_sec: 80,
    speed_kmh: 8.3,
    trips_per_day: 21,
    minutes_lost_per_day: 28,
    enough_samples: true,
  },
  weekly_excess_sec: [null, 80, 80, 80],
  slow_weeks: 3,
  slow_since: "2026-03-23",
};

const fine: SlowZoneSegmentData = { ...slow, from_stop_id: "S2", from_stop_name: "八丁堀", to_stop_id: "S3", to_stop_name: "胡町", slow_weeks: 0, slow_since: null };

const response: SlowZonesResponse = {
  available: true,
  unavailable_reason: null,
  last_checked: null,
  value_basis: "feed_prediction",
  day_type: "weekday",
  time_band: "morning",
  weeks: ["2026-03-16", "2026-03-23", "2026-03-30", "2026-04-06"],
  thresholds: { excess_sec: 60, min_scheduled_sec: 60, min_weeks: 3, min_samples: 21 },
  segments: [slow, fine],
  slow_zones: [["S1", "S2"]],
};

function stub(data: SlowZonesResponse | undefined) {
  return vi.spyOn(slowZonesApi, "useSlowZones").mockReturnValue({
    data,
    error: null,
    refetch: vi.fn(),
  } as unknown as UseQueryResult<SlowZonesResponse>);
}

function open() {
  const router = createMemoryRouter([{ path: "agencies/:agencyId/why", element: <SlowZonesView /> }], {
    initialEntries: ["/agencies/9/why?by=segments"],
  });
  renderWithProviders(<RouterProvider router={router} />);
}

describe("SlowZonesView", () => {
  beforeEach(async () => {
    maps.length = 0;
    await i18n.changeLanguage("en");
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks for weekday mornings by default and passes a chosen band and day type on", async () => {
    const hook = stub(response);
    open();
    expect(hook).toHaveBeenCalledWith(9, "morning", "weekday");
    await userEvent.selectOptions(screen.getByLabelText("Departure time band"), "evening");
    await userEvent.selectOptions(screen.getByLabelText("Days"), "weekend_holiday");
    expect(hook).toHaveBeenLastCalledWith(9, "evening", "weekend_holiday");
  });

  it("lists only the slow zones, as feed-based estimates, with a CSV of them", () => {
    stub(response);
    open();
    expect(screen.getByText(/estimates from the delays the operator's feed predicted/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(rows).toHaveLength(2);
    expect(rows[1]).toHaveTextContent("紙屋町 → 八丁堀");
    expect(rows[1]).toHaveTextContent("R1, R2");
    expect(rows[1]).toHaveTextContent("28.0");
    expect(screen.queryByText("八丁堀 → 胡町")).not.toBeInTheDocument();
    const csv = screen.getByRole("link", { name: /csv/i });
    expect(csv.getAttribute("href")).toContain("/api/9/segments/slow-zones?time_band=morning&day_type=weekday&format=csv");
  });

  it("describes a segment clicked on the map", () => {
    stub(response);
    open();
    const click = maps[0].handlers.click[0];
    act(() => click({ features: [{ properties: { key: "S2\u0000S3" } }] }));
    expect(screen.getByText(/八丁堀 → 胡町 \(R1, R2\), newest week: median 200 s/)).toBeInTheDocument();
  });

  it("says when nothing has stayed slow", () => {
    stub({ ...response, segments: [fine], slow_zones: [] });
    open();
    expect(screen.getByText("No segment has stayed slow in these weeks.")).toBeInTheDocument();
  });

  it("says why a feed that sends no arrivals shows nothing", () => {
    stub({ ...response, available: false, unavailable_reason: "feed_unsupported", segments: [], slow_zones: [] });
    open();
    expect(screen.getByText(/can't be taken from this agency's feed/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
