import { describe, it, expect, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import { TrendFocusProvider } from "./TrendFocusContext";
import { isoDow, type TrendFocus, type TrendFocusSource, type TrendMark } from "./trendFocus";
import { DailyChart } from "./DailyChart";
import { HourlyHeatmap, type HourlyCell } from "./HourlyHeatmap";
import { BandGrid } from "./DowBandGrid";
import { HeatSurface } from "./HeatSurface";
import { BAND_ORDER, type ForecastOverviewGridCell, type TrendDay } from "../../api/types";
import { DELAY_THRESHOLDS } from "../../styles/tokens";

// Each chart is wrapped so its own function body counts its renders. React's
// <Profiler> cannot stand in for this: a re-render a consumer picks up through
// context propagation never flags the Profiler above it, so a Profiler count
// stays flat even while every linked chart re-renders on each hover.
const { renders, countRenders } = vi.hoisted(() => {
  const renders = { daily: 0, hourly: 0, dow: 0 };
  function countRenders<P>(render: (props: P) => ReactNode, id: keyof typeof renders) {
    return (props: P) => {
      renders[id] += 1;
      return render(props);
    };
  }
  return { renders, countRenders };
});

vi.mock("./DailyChart", async (importOriginal) => {
  const mod = await importOriginal<typeof import("./DailyChart")>();
  return { ...mod, DailyChart: countRenders(mod.DailyChart, "daily") };
});
vi.mock("./HourlyHeatmap", async (importOriginal) => {
  const mod = await importOriginal<typeof import("./HourlyHeatmap")>();
  return { ...mod, HourlyHeatmap: countRenders(mod.HourlyHeatmap, "hourly") };
});
vi.mock("./DowBandGrid", async (importOriginal) => {
  const mod = await importOriginal<typeof import("./DowBandGrid")>();
  return { ...mod, BandGrid: countRenders(mod.BandGrid, "dow") };
});

// 2026-05-18 is a Monday; 2026-05-25 is the following Monday, so the daily
// chart carries two marks that agree on weekday and disagree on date.
const MON = "2026-05-18";
const TUE = "2026-05-19";
const MON2 = "2026-05-25";

const DAYS: TrendDay[] = [MON, TUE, MON2].map((date) => ({ date, avg_min: 2, samples: 100, top_offenders: [] }));
const CELLS: HourlyCell[] = [MON, TUE, MON2].flatMap((date) => [8, 9].map((hour) => ({ date, hour, avg_min: 2, samples: 100 })));

function grid(): ForecastOverviewGridCell[] {
  const out: ForecastOverviewGridCell[] = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) out.push({ dow, band, expected_avg_min: 2, samples: 200, low_confidence: false });
  }
  return out;
}

/** The reference semantics the CSS rules must reproduce: a mark dims when it
 *  disagrees with another chart's focus on a dimension they both carry. */
function isFocusDimmed(focus: TrendFocus | null, mark: TrendMark, viewer: TrendFocusSource): boolean {
  if (!focus || focus.source === viewer) return false;
  for (const key of ["date", "dow", "hour"] as const) {
    const f = focus[key];
    const m = mark[key];
    if (f === undefined || m === undefined) continue;
    if (f !== m) return true;
  }
  return false;
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
          <BandGrid grid={grid()} bandLabel={(b) => b} dayLabel={(d) => `dow${d}`} colorFor={() => "var(--accent)"} onTip={() => {}} onLeave={() => {}} />
        </div>
      </TrendFocusProvider>
    </MemoryRouter>,
  );
}

/** Renders per chart since `before`. */
function rendersSince(before: typeof renders) {
  return { daily: renders.daily - before.daily, hourly: renders.hourly - before.hourly, dow: renders.dow - before.dow };
}

function wrapper(container: HTMLElement): HTMLElement {
  return container.querySelector<HTMLElement>(".trend-focus")!;
}

function emittedSelectors(container: HTMLElement): string[] {
  const css = wrapper(container).querySelector("style")!.textContent ?? "";
  return css.split("}").map((r) => r.split("{")[0].trim()).filter(Boolean);
}

function dimmedByCss(container: HTMLElement, mark: Element): boolean {
  return emittedSelectors(container).some((sel) => mark.matches(sel));
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
function hoverDay(container: HTMLElement, index: number) {
  fireEvent.mouseEnter(within(container).getByTestId("daily").querySelector(`[data-testid='daily-day'][data-index='${index}']`)!);
}

describe("TrendFocus crossfilter (CSS attribute dimming)", () => {
  it("publishes the hovered day on the wrapper and dims heatmap cells on other dates", () => {
    const { container } = renderLinked();
    hoverDay(container, 0);
    const w = wrapper(container);
    expect(w.dataset.focusSource).toBe("daily");
    expect(w.dataset.focusDate).toBe(MON);
    expect(w.dataset.focusDow).toBe("1");
    expect(dimmedByCss(container, heatCell(container, MON, 8))).toBe(false);
    expect(dimmedByCss(container, heatCell(container, TUE, 8))).toBe(true);
    // Same weekday, other date: only the generated date rule can catch this.
    expect(dimmedByCss(container, heatCell(container, MON2, 8))).toBe(true);
  });

  it("clears every focus attribute when the pointer leaves", () => {
    const { container } = renderLinked();
    const rect = within(container).getByTestId("daily").querySelector("[data-testid='daily-day'][data-index='0']")!;
    fireEvent.mouseEnter(rect);
    fireEvent.mouseLeave(rect);
    const w = wrapper(container);
    expect(w.dataset.focusSource).toBeUndefined();
    expect(w.dataset.focusDate).toBeUndefined();
    expect(w.dataset.focusDow).toBeUndefined();
    expect(dimmedByCss(container, heatCell(container, TUE, 8))).toBe(false);
  });

  it("never dims the chart the focus came from", () => {
    const { container } = renderLinked();
    fireEvent.mouseEnter(heatCell(container, MON, 8));
    expect(dimmedByCss(container, heatCell(container, TUE, 9))).toBe(false);
    expect(dimmedByCss(container, dailyBar(container, 1))).toBe(true);
  });

  it("dims by weekday from a day-of-week band cell", () => {
    const { container } = renderLinked();
    fireEvent.mouseEnter(dowCell(container, 2));
    expect(dimmedByCss(container, dailyBar(container, 0))).toBe(true);
    expect(dimmedByCss(container, dailyBar(container, 1))).toBe(false);
    expect(dimmedByCss(container, heatCell(container, MON, 8))).toBe(true);
    expect(dimmedByCss(container, heatCell(container, TUE, 8))).toBe(false);
  });

  it("agrees with the reference semantics for every mark under every hover", () => {
    const { container } = renderLinked();
    const hovers: Array<[() => void, TrendFocus]> = [
      [() => hoverDay(container, 0), { source: "daily", date: MON, dow: 1 }],
      [() => fireEvent.mouseEnter(heatCell(container, TUE, 9)), { source: "hourly", hour: 9, dow: 2 }],
      [() => fireEvent.mouseEnter(dowCell(container, 1)), { source: "dow", dow: 1 }],
    ];
    for (const [hover, focus] of hovers) {
      hover();
      for (const [i, d] of DAYS.entries()) {
        expect(dimmedByCss(container, dailyBar(container, i))).toBe(isFocusDimmed(focus, { date: d.date, dow: isoDow(d.date) }, "daily"));
      }
      for (const c of CELLS) {
        expect(dimmedByCss(container, heatCell(container, c.date, c.hour))).toBe(isFocusDimmed(focus, { date: c.date, hour: c.hour, dow: isoDow(c.date) }, "hourly"));
      }
      for (let dow = 1; dow <= 7; dow++) {
        expect(dimmedByCss(container, dowCell(container, dow))).toBe(isFocusDimmed(focus, { dow }, "dow"));
      }
    }
  });

  it("agrees with the reference semantics in the trend view's own trio, with the heat surface as the weekday chart", () => {
    const { container } = renderWithProviders(
      <MemoryRouter>
        <TrendFocusProvider>
          <div data-testid="daily">
            <DailyChart days={DAYS} />
          </div>
          <HourlyHeatmap cells={CELLS} />
          <HeatSurface hourly={CELLS} grid={grid()} worst={null} rangeDays={14} />
        </TrendFocusProvider>
      </MemoryRouter>,
    );
    const surfaceCell = (dow: number, hour: number) => container.querySelector(`.heat-surface__cell[data-dow='${dow}'][data-hour='${hour}']`)!;
    const hovers: Array<[() => void, TrendFocus]> = [
      [() => hoverDay(container, 0), { source: "daily", date: MON, dow: 1 }],
      [() => fireEvent.mouseEnter(heatCell(container, TUE, 9)), { source: "hourly", hour: 9, dow: 2 }],
      [() => fireEvent.mouseOver(surfaceCell(1, 8)), { source: "dow", dow: 1 }],
    ];
    for (const [hover, focus] of hovers) {
      hover();
      for (const [i, d] of DAYS.entries()) {
        expect(dimmedByCss(container, dailyBar(container, i))).toBe(isFocusDimmed(focus, { date: d.date, dow: isoDow(d.date) }, "daily"));
      }
      for (const c of CELLS) {
        expect(dimmedByCss(container, heatCell(container, c.date, c.hour))).toBe(isFocusDimmed(focus, { date: c.date, hour: c.hour, dow: isoDow(c.date) }, "hourly"));
      }
      for (let dow = 1; dow <= 7; dow++) {
        expect(dimmedByCss(container, surfaceCell(dow, 8))).toBe(isFocusDimmed(focus, { dow }, "dow"));
      }
    }
  });

  it("re-renders no chart for another chart's hover: only the provider's style element changes", () => {
    const { container } = renderLinked();
    // DailyChart and HourlyHeatmap hold hover state for their own tooltips,
    // so the chart under the pointer renders once; BandGrid holds none. The
    // two charts not under the pointer must not render at all.
    let before = { ...renders };
    hoverDay(container, 0);
    expect(rendersSince(before)).toEqual({ daily: 1, hourly: 0, dow: 0 });
    before = { ...renders };
    fireEvent.mouseEnter(heatCell(container, TUE, 9));
    expect(rendersSince(before)).toEqual({ daily: 0, hourly: 1, dow: 0 });
    before = { ...renders };
    fireEvent.mouseEnter(dowCell(container, 3));
    expect(rendersSince(before)).toEqual({ daily: 0, hourly: 0, dow: 0 });
  });

  it("routes each mark kind through its own dim channel", () => {
    const { container } = renderLinked();
    expect(dailyBar(container, 0).classList).toContain("focus-dim-opacity");
    expect((dailyBar(container, 0) as SVGElement).style.getPropertyValue("--mark-opacity")).toBe("0.7");
    expect(container.querySelector("[data-testid='daily-dot'][data-index='0']")!.classList).toContain("focus-dim-opacity");
    expect(heatCell(container, MON, 8).classList).toContain("focus-dim-fill");
    expect(dowCell(container, 1).classList).toContain("focus-dim-filter");
  });

  it("dims a severe outline together with its heat cell", () => {
    const { container } = renderWithProviders(
      <MemoryRouter>
        <TrendFocusProvider>
          <DailyChart days={DAYS} />
          <HourlyHeatmap
            cells={[
              { date: MON, hour: 8, avg_min: 2, samples: 100 },
              { date: TUE, hour: 8, avg_min: DELAY_THRESHOLDS.severe + 1, samples: 100 },
            ]}
          />
        </TrendFocusProvider>
      </MemoryRouter>,
    );
    fireEvent.mouseEnter(container.querySelector("[data-testid='daily-day'][data-index='0']")!);
    const outline = container.querySelector("[data-testid='heat-severe-outline']")!;
    expect(outline.classList).toContain("focus-dim-stroke");
    expect(dimmedByCss(container, outline)).toBe(true);
  });

  it("leaves a grid rendered outside the provider inert", () => {
    const { container } = renderWithProviders(
      <BandGrid grid={grid()} bandLabel={(b) => b} dayLabel={(d) => `dow${d}`} colorFor={() => "var(--accent)"} onTip={() => {}} onLeave={() => {}} />,
    );
    fireEvent.mouseEnter(dowCell(container, 2));
    expect(container.querySelector(".trend-focus")).toBeNull();
    expect(dowCell(container, 1).style.getPropertyValue("--cell-opacity")).toBe("1");
  });
});
