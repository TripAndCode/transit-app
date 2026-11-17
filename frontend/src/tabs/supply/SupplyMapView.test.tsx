import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { UseQueryResult } from "@tanstack/react-query";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import * as supplyApi from "./supplyApi";
import type { SupplyMapResponse } from "./supplyApi";
import { SupplyMapView } from "./SupplyMapView";

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

const response: SupplyMapResponse = {
  date: "2026-04-06",
  day_type: "weekday",
  from_hour: 10,
  to_hour: 16,
  agencies: [
    { agency_id: 1, agency_name: "広島バス", status: "ok", static_version_id: "gtfs_static_20260401", typical_day: "2026-04-06" },
    { agency_id: 2, agency_name: "芸陽バス", status: "not_built", static_version_id: "gtfs_static_20260402", typical_day: null },
    { agency_id: 3, agency_name: "備北交通", status: "no_timetable", static_version_id: null, typical_day: null },
  ],
  stops: [
    {
      agency_id: 1, stop_id: "S1", stop_name: "紙屋町", lat: 34.39, lon: 132.45,
      departures: 24, per_hour: 4, longest_gap_min: 25, first_sec: 6 * 3600, last_sec: 24 * 3600 + 20 * 60,
    },
    {
      agency_id: 1, stop_id: "S2", stop_name: "山奥", lat: 34.6, lon: 132.7,
      departures: 1, per_hour: 0.17, longest_gap_min: 240, first_sec: 7 * 3600, last_sec: 12 * 3600,
    },
    {
      agency_id: 1, stop_id: "S3", stop_name: null, lat: null, lon: null,
      departures: 0, per_hour: 0, longest_gap_min: 360, first_sec: null, last_sec: null,
    },
  ],
};

function stubSupply(data: SupplyMapResponse | undefined) {
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
    expect(screen.getByText("3 stops of 1 operators, 2 of them with a gap over 60 min")).toBeInTheDocument();
    expect(screen.getByText("1 stops have no position in the current timetable and are not drawn.")).toBeInTheDocument();
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
    expect(screen.getByText("3 stops of 1 operators, 3 of them with a gap over 20 min")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Day type"), "saturday");
    await userEvent.selectOptions(screen.getByLabelText("To"), "25");
    expect(hook).toHaveBeenLastCalledWith({ date: "", dayType: "saturday", fromHour: 10, toHour: 25 });
  });

  it("says when the band ends before it starts", async () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    await userEvent.selectOptions(screen.getByLabelText("From"), "16");
    await userEvent.selectOptions(screen.getByLabelText("To"), "10");
    expect(screen.getByRole("alert")).toHaveTextContent("The end must be after the start.");
  });

  it("describes a stop clicked on the map, its last departure past midnight", () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    const click = maps[0].handlers.click[0];
    act(() => click({ features: [{ properties: { key: "1:S1" } }] }));
    expect(
      screen.getByText("紙屋町: 24 departures in the hours (4 an hour), longest gap 25 min, first 6:00, last 24:20"),
    ).toBeInTheDocument();
  });

  it("switches the legend to the gap threshold", async () => {
    stubSupply(response);
    renderWithProviders(<SupplyMapView />);
    expect(screen.getByText("3 or more an hour")).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText("Show"), "gap");
    expect(screen.getByText("A gap over 60 min")).toBeInTheDocument();
    expect(screen.queryByText("3 or more an hour")).not.toBeInTheDocument();
  });

  it("says when no operator's stops can be read", () => {
    stubSupply({ ...response, agencies: [response.agencies[2]], stops: [] });
    renderWithProviders(<SupplyMapView />);
    expect(screen.getByText("No operator's stops can be read for this date and day type.")).toBeInTheDocument();
  });
});
