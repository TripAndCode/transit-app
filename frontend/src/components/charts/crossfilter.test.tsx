import { describe, it, expect } from "vitest";
import { fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { TrendFocusProvider } from "./TrendFocusContext";
import { DIM_OPACITY } from "./trendFocus";
import { DailyChart } from "./DailyChart";
import { HourlyHeatmap, type HourlyCell } from "./HourlyHeatmap";
import { BandGrid } from "./DowBandGrid";
import { BAND_ORDER, type ForecastOverviewGridCell, type TrendDay } from "../../api/types";

// 2026-05-18 is a Monday, so these two days are ISO weekdays 1 and 2.
const MON = "2026-05-18";
const TUE = "2026-05-19";

const DAYS: TrendDay[] = [MON, TUE].map((date) => ({ date, avg_min: 2, samples: 100, top_offenders: [] }));

const CELLS: HourlyCell[] = [MON, TUE].flatMap((date) =>
  [8, 9].map((hour) => ({ date, hour, avg_min: 2, samples: 100 })),
);

function grid(): ForecastOverviewGridCell[] {
  const out: ForecastOverviewGridCell[] = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) {
      out.push({ dow, band, expected_avg_min: 2, samples: 200, low_confidence: false });
    }
  }
  return out;
}

function renderLinked() {
  return renderWithProviders(
    <MemoryRouter>
      <TrendFocusProvider>
        <div data-testid="daily">
          <DailyChart days={DAYS} />
        </div>
        <div data-testid="hourly">
          <HourlyHeatmap cells={CELLS} />
        </div>
        <div data-testid="dow">
          <BandGrid
            grid={grid()}
            bandLabel={(b) => b}
            dayLabel={(d) => `dow${d}`}
            axisMin="m"
            colorFor={() => "var(--accent)"}
            onTip={() => {}}
            onLeave={() => {}}
          />
        </div>
      </TrendFocusProvider>
    </MemoryRouter>,
  );
}

function heatCell(container: HTMLElement, date: string, hour: number): Element {
  return container.querySelector(`[data-testid='heat-cell'][data-date='${date}'][data-hour='${hour}']`)!;
}

function dailyBar(container: HTMLElement, index: number): Element {
  return container.querySelector(`[data-testid='daily-bar'][data-index='${index}']`)!;
}

function dowCell(container: HTMLElement, dow: number): HTMLElement {
  return container.querySelector<HTMLElement>(`[data-testid='ov-band-cell'][data-dow='${dow}']`)!;
}

describe("TrendFocus crossfilter", () => {
  it("dims heatmap cells outside the hovered day", () => {
    const { container } = renderLinked();
    const daily = within(container).getByTestId("daily");
    fireEvent.mouseEnter(daily.querySelector("[data-testid='daily-day'][data-index='0']")!);

    expect(heatCell(container, MON, 8).getAttribute("fill-opacity")).toBe("1");
    expect(heatCell(container, TUE, 8).getAttribute("fill-opacity")).toBe(String(DIM_OPACITY));
  });

  it("restores every mark when the pointer leaves the chart", () => {
    const { container } = renderLinked();
    const daily = within(container).getByTestId("daily");
    const rect = daily.querySelector("[data-testid='daily-day'][data-index='0']")!;
    fireEvent.mouseEnter(rect);
    fireEvent.mouseLeave(rect);
    expect(heatCell(container, TUE, 8).getAttribute("fill-opacity")).toBe("1");
  });

  it("dims the daily chart by weekday when an hour cell is hovered", () => {
    const { container } = renderLinked();
    fireEvent.mouseEnter(heatCell(container, MON, 8));

    expect(dailyBar(container, 0).getAttribute("opacity")).not.toBe(String(DIM_OPACITY));
    expect(dailyBar(container, 1).getAttribute("opacity")).toBe(String(DIM_OPACITY));
  });

  it("never dims the chart the focus came from", () => {
    const { container } = renderLinked();
    fireEvent.mouseEnter(heatCell(container, MON, 8));
    expect(heatCell(container, TUE, 9).getAttribute("fill-opacity")).toBe("1");
  });

  it("dims by weekday when a day-of-week band cell is hovered", () => {
    const { container } = renderLinked();
    fireEvent.mouseEnter(dowCell(container, 2));

    expect(dailyBar(container, 0).getAttribute("opacity")).toBe(String(DIM_OPACITY));
    expect(dailyBar(container, 1).getAttribute("opacity")).not.toBe(String(DIM_OPACITY));
    expect(heatCell(container, MON, 8).getAttribute("fill-opacity")).toBe(String(DIM_OPACITY));
    expect(heatCell(container, TUE, 8).getAttribute("fill-opacity")).toBe("1");
  });

  it("leaves a grid rendered outside the provider inert", () => {
    const { container } = renderWithProviders(
      <BandGrid
        grid={grid()}
        bandLabel={(b) => b}
        dayLabel={(d) => `dow${d}`}
        axisMin="m"
        colorFor={() => "var(--accent)"}
        onTip={() => {}}
        onLeave={() => {}}
      />,
    );
    fireEvent.mouseEnter(dowCell(container, 2));
    expect(dowCell(container, 1).style.getPropertyValue("--cell-opacity")).toBe("1");
  });
});
