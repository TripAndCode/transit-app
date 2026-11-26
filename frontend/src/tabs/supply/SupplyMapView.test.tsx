import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import * as supplyApi from "./supplyApi";
import type { SupplyMapResponse, SupplyStopDetail } from "./supplyApi";
import { SupplyMapView } from "./SupplyMapView";

type Handler = (event?: unknown) => void;
type Captured = {
  handlers: Record<string, Handler[]>;
  setStyle: ReturnType<typeof vi.fn>;
  setPaintProperty: ReturnType<typeof vi.fn>;
};
const maps: Captured[] = [];

vi.mock("maplibre-gl", async () => {
  const mock = await import("../../test/maplibreMock");
  class CapturedMap extends mock.MockMap {
    setStyle = vi.fn();
    setPaintProperty = vi.fn();
    // The layer the view adds is there to repaint.
    getLayer = vi.fn(() => ({}) as unknown as undefined);
    constructor(options: Record<string, unknown>) {
      super(options);
      maps.push(this as unknown as Captured);
    }
  }
  return { ...mock, Map: CapturedMap };
});

const response: SupplyMapResponse = {
  date: "2026-04-06",
  day_type: "weekday",
  from_hour: 10,
  to_hour: 16,
  agencies: [
    {
      agency_id: 1,
      agency_name: "広島バス",
      status: "ok",
      static_version_id: "gtfs_static_20260401",
      typical_day: "2026-04-06",
      stops: {
        stop_id: ["S1", "S2", "S3"],
        lat: [34.39, 34.6, null],
        lon: [132.45, 132.7, null],
        departures: [24, 1, 0],
        longest_gap_min: [25, 240, 360],
      },
    },
    { agency_id: 2, agency_name: "芸陽バス", status: "not_built", static_version_id: "gtfs_static_20260402", typical_day: null, stops: null },
    { agency_id: 3, agency_name: "備北交通", status: "no_timetable", static_version_id: null, typical_day: null, stops: null },
  ],
};

const detail: SupplyStopDetail = {
  agency_id: 1,
  stop_id: "S1",
  stop_name: "紙屋町",
  departures: 24,
  per_hour: 4,
  longest_gap_min: 25,
  first_sec: 6 * 3600,
  last_sec: 24 * 3600 + 20 * 60,
  hours: [
    { hour: 10, departures: 4, observed: null },
    { hour: 11, departures: 4, observed: null },
  ],
  observed_window: null,
  definition: null,
};

const observed: SupplyStopDetail = {
  ...detail,
  hours: [
    {
      hour: 10,
      departures: 4,
      observed: { departures: 6, per_day: 2, on_time: 3, on_time_pct: 50, uncertain: true, avg_delay_min: 3.1 },
    },
    {
      hour: 11,
      departures: 4,
      observed: { departures: 0, per_day: 0, on_time: 0, on_time_pct: null, uncertain: false, avg_delay_min: null },
    },
  ],
  observed_window: { first: "2026-03-30", last: "2026-03-31", days: 3 },
  definition: null,
};

function stubSupply(data: SupplyMapResponse | undefined, opened: SupplyStopDetail = detail) {
  vi.spyOn(supplyApi, "useSupplyStop").mockImplementation(
    (_query, stop) => ({ data: stop?.stopId === "S1" ? opened : undefined }) as unknown as UseQueryResult<SupplyStopDetail>,
  );
  return vi.spyOn(supplyApi, "useNetworkSupply").mockReturnValue({
    data,
    error: null,
    refetch: vi.fn(),
  } as unknown as UseQueryResult<SupplyMapResponse>);
}

describe("SupplyMapView", () => {
  beforeEach(async () => {
    maps.length = 0;
    await i18n.changeLanguage("en");
  });
  afterEach(() => vi.restoreAllMocks());

  it("asks for a weekday 10:00 to 16:00 by default and summarises the stops", () => {
    const hook = stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    expect(hook).toHaveBeenCalledWith({ date: "", dayType: "weekday", fromHour: 10, toHour: 16 });
    expect(screen.getByText("Operators read: 1 · stops: 3 · with a gap over 60 min: 2")).toBeInTheDocument();
    expect(screen.getByText("1 stop has no position in the current timetable and is not drawn.")).toBeInTheDocument();
  });

  it("names the operators it cannot show and why, never as zero", () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    expect(screen.getByText("芸陽バス: this timetable has not been computed yet")).toBeInTheDocument();
    expect(screen.getByText("備北交通: no timetable in force on this date")).toBeInTheDocument();
    expect(screen.getByText("広島バス: gtfs_static_20260401, typical day 2026-04-06")).toBeInTheDocument();
  });

  it("recounts the gaps against a chosen threshold and passes a chosen band on", async () => {
    const hook = stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    await userEvent.selectOptions(screen.getByLabelText("Highlight gaps over"), "20");
    expect(screen.getByText("Operators read: 1 · stops: 3 · with a gap over 20 min: 3")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Day type"), "saturday");
    await userEvent.selectOptions(screen.getByLabelText("To"), "25");
    expect(hook).toHaveBeenLastCalledWith({ date: "", dayType: "saturday", fromHour: 10, toHour: 25 });
  });

  it("shows nothing of the last band once the band ends before it starts", async () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    await userEvent.selectOptions(screen.getByLabelText("From"), "16");
    await userEvent.selectOptions(screen.getByLabelText("To"), "10");
    expect(screen.getByRole("alert")).toHaveTextContent("The end must be after the start.");
    expect(screen.queryByText(/^Operators read:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/has no position/)).not.toBeInTheDocument();
    expect(screen.queryByText(/芸陽バス/)).not.toBeInTheDocument();
  });

  it("opens a stop clicked on the map, its last departure past midnight", () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    const click = maps[0].handlers.click[0];
    act(() => click({ features: [{ properties: { key: "1:S1" } }] }));
    expect(
      screen.getByText("紙屋町: 24 departures in the hours (4 an hour), longest gap 25 min, first 6:00, last 24:20"),
    ).toBeInTheDocument();
    expect(supplyApi.useSupplyStop).toHaveBeenLastCalledWith(
      { date: "", dayType: "weekday", fromHour: 10, toHour: 16 },
      { agencyId: 1, stopId: "S1" },
    );
  });

  it("sets dashes beside each hour's planned departures when no day of its day type ran on the same timetable", () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    act(() => maps[0].handlers.click[0]({ features: [{ properties: { key: "1:S1" } }] }));
    expect(screen.getAllByRole("row").map((row) => row.textContent)).toEqual([
      "HourPlannedObserved a dayOn timeMean delay",
      "10:004———",
      "11:004———",
    ]);
    expect(screen.getByText(/^Nothing observed:/)).toBeInTheDocument();
    expect(screen.queryByText(/^On time counts the observed departures only/)).not.toBeInTheDocument();
  });

  it("sets each hour's observed departures and on-time beside its planned ones", () => {
    stubSupply(response, observed);
    renderWithProviders(<SupplyMapView />);
    act(() => maps[0].handlers.click[0]({ features: [{ properties: { key: "1:S1" } }] }));
    const rows = screen.getAllByRole("row").map((row) => row.textContent);
    expect(rows).toEqual([
      "HourPlannedObserved a dayOn timeMean delay",
      "10:0042.050.0% (wide margin)3.1 min",
      "11:0040.0——",
    ]);
    expect(screen.getByText("50.0%").closest("td")).toHaveAttribute("title", "6 observed over 3 days, 3 on time");
    expect(
      screen.getByText(/^Observed on the 3 days of this day type on the same timetable between .+\. The definition below covers that whole period\.$/),
    ).toBeInTheDocument();
    expect(screen.getByText(/^On time counts the observed departures only/)).toBeInTheDocument();
  });

  it("repaints rather than reloading the stops when the mode changes, and switches the legend", async () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    expect(screen.getByText("3 or more an hour")).toBeInTheDocument();
    const map = maps[0];
    map.setPaintProperty.mockClear();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "gap");
    expect(screen.getByText("A gap over 60 min")).toBeInTheDocument();
    expect(screen.queryByText("3 or more an hour")).not.toBeInTheDocument();
    expect(map.setPaintProperty).toHaveBeenCalledWith(
      "supply-stops-circles",
      "circle-opacity",
      ["case", [">", ["get", "gap"], 60], 0.9, 0.18],
    );
  });

  it("restyles the basemap in the new language", async () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    await act(() => i18n.changeLanguage("ja"));
    expect(maps[0].setStyle).toHaveBeenCalledTimes(1);
    await act(() => i18n.changeLanguage("en"));
  });

  it("says when no operator's stops can be read", () => {
    stubSupply({ ...response, agencies: [response.agencies[2]] });
    renderWithProviders(<SupplyMapView />);
    expect(screen.getByText("No operator's stops can be read for this date and day type.")).toBeInTheDocument();
  });
});
