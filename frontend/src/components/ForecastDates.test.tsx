import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { ForecastHistory, ForecastPeriodNote } from "./ForecastDates";
import * as hooks from "../api/hooks";
import { BAND_ORDER } from "../api/timeBands";
import { type ForecastDay, type ForecastOverview } from "../api/types";

/** Tue 10/27 .. Sun 11/1: the six days after a Monday. */
const SIX_DAYS: ForecastDay[] = [2, 3, 4, 5, 6, 7].map((dow) => ({
  dow,
  service_date: `2026-${dow <= 6 ? `10-${25 + dow}` : "11-01"}`,
}));

function grid(mlDows: number[]) {
  return Array.from({ length: 7 }, (_, i) => i + 1).flatMap((dow) =>
    BAND_ORDER.map((band) => ({
      dow,
      band,
      expected_avg_min: 2,
      samples: 200,
      low_confidence: false,
      source: mlDows.includes(dow) ? ("ml" as const) : ("baseline" as const),
    })),
  );
}

function overview(partial: Partial<ForecastOverview> = {}): ForecastOverview {
  return {
    grid: grid([]),
    worst: null,
    routes: [],
    forecast_days: [],
    history_from: "2026-07-12",
    history_to: "2026-10-23",
    recent_from: "2026-10-17",
    recent_to: "2026-10-23",
    disclaimer: "",
    ...partial,
  };
}

function mockData(ov: ForecastOverview | undefined, hm: unknown = undefined) {
  vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: ov } as never);
  vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: hm } as never);
}

describe("ForecastPeriodNote", () => {
  it("names the dates the forecast covers, and what the rest of the grid shows", () => {
    mockData(overview({ grid: grid([2, 3, 4, 5, 6, 7]), forecast_days: SIX_DAYS }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    const note = screen.getByTestId("fc-period");
    expect(note).toHaveTextContent("Forecast for Tue, Oct 27 – Sun, Nov 1.");
    expect(note).toHaveTextContent("Days and times without a forecast show the average of measurements over Jul 12 – Oct 23.");
  });

  it("says nothing about past averages when every shown cell is a forecast", () => {
    mockData(overview({ grid: grid([1, 2, 3, 4, 5, 6, 7]), forecast_days: [{ dow: 1, service_date: "2026-10-26" }, ...SIX_DAYS] }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(screen.getByTestId("fc-period")).toHaveTextContent(/^Forecast for Mon, Oct 26 – Sun, Nov 1\.$/);
  });

  it("calls a grid with no forecast a typical week over the measured period, not a forecast for dates", () => {
    mockData(overview({ history_from: "2026-09-10" }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(screen.getByTestId("fc-period")).toHaveTextContent(
      "Not a forecast for particular dates: the average by day and time of day of measurements over Sep 10 – Oct 23.",
    );
  });

  it("names a chosen route's own forecast dates and measured period", () => {
    mockData(overview({ forecast_days: SIX_DAYS }), {
      route: "100",
      cells: [
        { dow: 2, hour: 8, expected_avg_min: 3, samples: 100, low_confidence: false, source: "ml" },
        { dow: 1, hour: 8, expected_avg_min: 3, samples: 100, low_confidence: false, source: "baseline" },
      ],
      forecast_days: [{ dow: 2, service_date: "2026-10-27" }],
      history_from: "2026-08-01",
      history_to: "2026-10-20",
      disclaimer: "",
    });
    renderWithProviders(<ForecastPeriodNote aid={1} route="100" />);
    const note = screen.getByTestId("fc-period");
    expect(note).toHaveTextContent("Forecast for Tue, Oct 27.");
    expect(note).toHaveTextContent("measurements over Aug 1 – Oct 20.");
  });

  it("falls back to undated wording when the measured period is unknown", () => {
    mockData(overview({ grid: grid([2]), forecast_days: [SIX_DAYS[0]], history_from: null, history_to: null, recent_from: null, recent_to: null }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(screen.getByTestId("fc-period")).toHaveTextContent("Days and times without a forecast show the average of past measurements.");
  });

  it("reads only the chosen route's forecast on a route's view, never the agency overview", () => {
    mockData(overview(), { route: "100", cells: [], forecast_days: [], history_from: null, history_to: null, disclaimer: "" });
    renderWithProviders(<ForecastPeriodNote aid={1} route="100" />);
    expect(hooks.useForecastOverview).toHaveBeenCalledWith(null);
    expect(hooks.useForecastOverview).not.toHaveBeenCalledWith(1);
    expect(hooks.useForecastHeatmap).toHaveBeenCalledWith(1, "100");
  });

  it("names no period above a view that shows no figures, whatever period the aggregates span", () => {
    const empty = grid([]).map((c) => ({ ...c, expected_avg_min: null }));
    mockData(overview({ grid: empty, routes: [] }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(screen.queryByTestId("fc-period")).toBeNull();
  });

  it("renders nothing before the data arrives, or for an agency with neither forecast nor measurements", () => {
    mockData(undefined);
    const { container, unmount } = renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(container).toBeEmptyDOMElement();
    unmount();
    mockData(overview({ history_from: null, history_to: null, recent_from: null, recent_to: null }));
    renderWithProviders(<ForecastPeriodNote aid={1} route={null} />);
    expect(screen.queryByTestId("fc-period")).toBeNull();
  });
});

describe("ForecastHistory", () => {
  it("heads its content as past measurements over the window they cover", () => {
    renderWithProviders(
      <ForecastHistory from="2026-07-12" to="2026-10-21" recentFrom="2026-10-17" recentTo="2026-10-23">
        <p>routes</p>
      </ForecastHistory>,
    );
    const section = screen.getByRole("region", { name: "Past measurements (Jul 12 – Oct 21)" });
    expect(section).toHaveTextContent("Each route's figure is the average of its measurements in this period.");
    expect(section).toHaveTextContent("The small line traces its daily average on the days with data over Oct 17 – Oct 23.");
    expect(section).toHaveTextContent("routes");
  });

  it("keeps the past-measurements heading when the window is unknown", () => {
    renderWithProviders(
      <ForecastHistory from={null} to={null} recentFrom={null} recentTo={null}>
        <p>routes</p>
      </ForecastHistory>,
    );
    const section = screen.getByRole("region", { name: "Past measurements" });
    expect(section).not.toHaveTextContent("small line");
  });
});
