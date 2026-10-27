import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import type { OverviewDayType, OverviewServiceSplitDay, OverviewServiceSplitEntry } from "../api/types";
import { ServiceSplit } from "./ServiceSplit";

type Figures = [avg_min: number | null, days: number, samples: number, insufficient?: boolean];

function entry(day_type: OverviewDayType, [avg_min, days, samples, insufficient = samples === 0]: Figures) {
  return { day_type, avg_min, days, samples, insufficient };
}

function split(weekday: Figures, saturday: Figures, sunday_holiday: Figures): OverviewServiceSplitEntry[] {
  return [entry("weekday", weekday), entry("saturday", saturday), entry("sunday_holiday", sunday_holiday)];
}

const TYPICAL = split([3.2, 18, 1_712_345], [3.0, 4, 234_567], [2.0, 4, 230_001]);

function rows(container: HTMLElement) {
  return [...container.querySelectorAll(".ov-svc-row")].map((row) => ({
    label: row.querySelector(".ov-svc-label")?.textContent,
    value: row.querySelector(".ov-svc-num")?.textContent,
    meta: row.querySelector(".ov-svc-meta")?.textContent,
    width: (row.querySelector(".ov-svc-fill") as HTMLElement | null)?.style.width,
  }));
}

describe("ServiceSplit card", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("shows the three day types in order, each with its mean, days and observations", () => {
    const { container } = renderWithProviders(<ServiceSplit service_split={TYPICAL} />);
    expect(rows(container).map(({ label, value, meta }) => [label, value, meta])).toEqual([
      ["Weekdays", "3.2 min", "18 days · 1.7M obs."],
      ["Saturdays", "3.0 min", "4 days · 235K obs."],
      ["Sundays & holidays", "2.0 min", "4 days · 230K obs."],
    ]);
  });

  it("draws every bar against one shared, rounded scale", () => {
    const { container } = renderWithProviders(<ServiceSplit service_split={TYPICAL} />);
    expect(rows(container).map((r) => r.width)).toEqual(["80%", "75%", "50%"]);
    const ticks = [...container.querySelectorAll(".ov-svc-tick")].map((e) => e.textContent);
    expect(ticks).toEqual(["0", "1", "2", "3", "4 min"]);
  });

  it("compares the slowest day type with the fastest", () => {
    renderWithProviders(<ServiceSplit service_split={TYPICAL} />);
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("names both day types that tie for slowest at the shown precision", () => {
    renderWithProviders(<ServiceSplit service_split={split([3.24, 18, 100], [3.16, 4, 100], [2.0, 4, 100])} />);
    expect(
      screen.getByText("Weekdays and Saturdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("names both day types that tie for fastest", () => {
    renderWithProviders(<ServiceSplit service_split={split([3.2, 18, 100], [2.0, 4, 100], [2.0, 4, 100])} />);
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Saturdays and Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("says there is no difference when the shown means are equal", () => {
    renderWithProviders(<ServiceSplit service_split={split([2.04, 18, 100], [1.96, 4, 100], [2.0, 4, 100])} />);
    expect(screen.getByText("Delay is about the same whatever the day type.")).toBeInTheDocument();
  });

  it("compares only the day types with data, and marks the others as having none", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={split([3.2, 18, 100], [null, 0, 0], [2.0, 4, 100])} />,
    );
    expect(rows(container)[1]).toEqual({ label: "Saturdays", value: "—", meta: "No data", width: undefined });
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("splits a feed with one weekend timetable into weekdays and weekends", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={[entry("weekday", [3.0, 18, 5000]), entry("weekend_holiday", [2.5, 8, 2000])]} />,
    );
    expect(rows(container).map((r) => r.label)).toEqual(["Weekdays", "Weekends & holidays"]);
    expect(
      screen.getByText("Weekdays run the latest, 0.5 min longer on average than Weekends & holidays."),
    ).toBeInTheDocument();
  });

  it("marks a day type with too little data instead of drawing it, and leaves it out of the comparison", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={split([3.2, 18, 5000], [6.0, 1, 900, true], [2.0, 4, 2000])} />,
    );
    const saturday = container.querySelectorAll(".ov-svc-row")[1];
    expect(saturday.querySelector(".ov-svc-insufficient")?.textContent).toBe("Not enough data");
    expect(saturday.querySelector(".ov-svc-track")).toBeNull();
    expect(rows(container)[1]).toMatchObject({ value: "6.0 min", meta: "1 day · 900 obs." });
    // The 6.0 neither stretches the scale nor counts as the slowest.
    expect(rows(container).map((r) => r.width)).toEqual(["80%", undefined, "50%"]);
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("tells no story when only one day type has enough data", () => {
    renderWithProviders(<ServiceSplit service_split={split([3.2, 18, 5000], [3.0, 2, 800, true], [null, 0, 0])} />);
    expect(screen.queryByText(/run the latest|about the same/)).toBeNull();
  });

  it("draws no scale when no day type has enough data for a bar", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={split([3.2, 2, 500, true], [null, 0, 0], [null, 0, 0])} />,
    );
    expect(container.querySelector(".ov-svc-scale")).toBeNull();
  });

  it("tells no story when only one day type has data", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={split([3.2, 18, 100], [null, 0, 0], [null, 0, 0])} />,
    );
    expect(container.querySelector(".ov-svc-story")).toBeNull();
    expect(rows(container)[0].width).toBe("80%");
  });

  it("keeps the shared scale through zero when a day type runs early", () => {
    const { container } = renderWithProviders(
      <ServiceSplit service_split={split([3.0, 18, 100], [-1.0, 4, 100], [null, 0, 0])} />,
    );
    const fills = [...container.querySelectorAll(".ov-svc-fill")] as HTMLElement[];
    // Axis -1..3: zero sits a quarter of the way in, and the early bar runs left from it.
    expect([fills[0].style.left, fills[0].style.width]).toEqual(["25%", "75%"]);
    expect([fills[1].style.left, fills[1].style.width]).toEqual(["0%", "25%"]);
    const ticks = [...container.querySelectorAll(".ov-svc-tick")].map((e) => e.textContent);
    expect(ticks).toEqual(["-1", "0", "1", "2", "3 min"]);
  });

  it("reads in Japanese with the counts in 万", async () => {
    await i18n.changeLanguage("ja");
    const { container } = renderWithProviders(<ServiceSplit service_split={TYPICAL} />);
    expect(rows(container)[0]).toMatchObject({ label: "平日", value: "3.2分", meta: "18日 · 171万件" }); // i18n-ignore: Japanese copy under test
    expect(rows(container).map((r) => r.label)).toEqual(["平日", "土曜", "日祝"]); // i18n-ignore: Japanese copy under test
    expect(screen.getByText("平日が最も遅れ、日祝より平均1.2分長い。")).toBeInTheDocument(); // i18n-ignore: Japanese copy under test
  });

  it("opens from the keyboard when clickable", () => {
    let opened = 0;
    renderWithProviders(<ServiceSplit service_split={TYPICAL} onClick={() => (opened += 1)} />);
    fireEvent.keyDown(screen.getByRole("button"), { key: "Enter" });
    expect(opened).toBe(1);
  });
});

function daily(n: number, start = 1): OverviewServiceSplitDay[] {
  // 2026-09-01 is a Tuesday; the 5th and 6th are a Saturday and a Sunday.
  return Array.from({ length: n }, (_, i) => {
    const day = start + i;
    const dow = new Date(Date.UTC(2026, 8, day)).getUTCDay();
    const day_type = dow === 6 ? "saturday" : dow === 0 ? "sunday_holiday" : "weekday";
    return { date: `2026-09-${String(day).padStart(2, "0")}`, day_type, avg_min: 2, samples: 1000 };
  });
}

describe("ServiceSplit modal chart", () => {
  it("draws one series per bucket the card shows", () => {
    const { container } = renderWithProviders(
      <ServiceSplit
        variant="modal"
        service_split={[entry("weekday", [3.0, 18, 5000]), entry("weekend_holiday", [2.5, 8, 2000])]}
        daily={[
          { date: "2026-09-04", day_type: "weekday", avg_min: 3.0, samples: 1000 },
          { date: "2026-09-05", day_type: "weekend_holiday", avg_min: 2.0, samples: 1000 },
        ]}
      />,
    );
    const legend = container.querySelector(".ov-svc-daily-legend") as HTMLElement;
    expect([...legend.children].map((e) => e.textContent)).toEqual(["Weekdays", "Weekends & holidays"]);
    expect(container.querySelectorAll('.ov-chart-plot svg circle[data-day-type="weekend_holiday"]')).toHaveLength(1);
  });

  it("draws one series per day type, each with its own legend entry", () => {
    const { container } = renderWithProviders(
      <ServiceSplit variant="modal" service_split={TYPICAL} daily={daily(7)} />,
    );
    const legend = container.querySelector(".ov-svc-daily-legend") as HTMLElement;
    expect(within(legend).getByText("Weekdays")).toBeInTheDocument();
    expect(within(legend).getByText("Saturdays")).toBeInTheDocument();
    expect(within(legend).getByText("Sundays & holidays")).toBeInTheDocument();
    // A single Saturday in the range still shows as a mark, not an invisible one-point path.
    expect(container.querySelectorAll('.ov-chart-plot svg circle[data-day-type="saturday"]')).toHaveLength(1);
    expect(container.querySelectorAll('.ov-chart-plot svg circle[data-day-type="weekday"]')).toHaveLength(5);
  });

  it("names the hovered date's day type in its tooltip, inside the plot box it measures from", () => {
    const { container } = renderWithProviders(
      <ServiceSplit
        variant="modal"
        service_split={TYPICAL}
        daily={[
          { date: "2026-09-05", day_type: "saturday", avg_min: 2.0, samples: 1000 },
          { date: "2026-09-06", day_type: "sunday_holiday", avg_min: 1.1, samples: 1000 },
        ]}
      />,
    );
    const svg = container.querySelector(".ov-chart-plot svg") as SVGSVGElement;
    svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.mouseMove(svg, { clientX: 390, clientY: 100 });
    const tooltip = container.querySelector(".ov-tooltip");
    expect(tooltip?.closest(".ov-chart-plot")).not.toBeNull();
    expect(tooltip?.textContent).toBe("9/6 — Sundays & holidays 1.1 min");
    fireEvent.mouseLeave(svg);
    expect(container.querySelector(".ov-tooltip")).toBeNull();
  });

  it("labels the x axis with locale short dates, leaving out the label that would crowd the last day's", () => {
    renderWithProviders(<ServiceSplit variant="modal" service_split={TYPICAL} daily={daily(29)} />);
    expect(screen.getByText("9/1")).toBeInTheDocument();
    expect(screen.getByText("9/29")).toBeInTheDocument();
    expect(screen.queryByText("9/28")).toBeNull();
  });
});

describe("ServiceSplit modal chart with early-running days", () => {
  function plotted(points: OverviewServiceSplitDay[]) {
    const { container } = renderWithProviders(
      <ServiceSplit variant="modal" service_split={TYPICAL} daily={points} />,
    );
    const gridYs = [...container.querySelectorAll(".ov-chart-plot svg line")].map((l) => Number(l.getAttribute("y1")));
    const markYs = [...container.querySelectorAll(".ov-chart-plot svg circle")].map((c) => Number(c.getAttribute("cy")));
    const ticks = [...container.querySelectorAll(".ov-svc-daily-label--y")].map((e) => e.textContent);
    return { top: Math.min(...gridYs), bottom: Math.max(...gridYs), markYs, ticks };
  }

  it("keeps negative means inside the plot, with the axis reaching below zero", () => {
    const { top, bottom, markYs, ticks } = plotted([
      { date: "2026-09-04", day_type: "weekday", avg_min: 2.0, samples: 1000 },
      { date: "2026-09-05", day_type: "saturday", avg_min: -1.0, samples: 1000 },
      { date: "2026-09-06", day_type: "sunday_holiday", avg_min: -3.0, samples: 1000 },
    ]);
    expect(markYs.length).toBe(3);
    for (const y of markYs) {
      expect(y).toBeGreaterThanOrEqual(top - 0.05);
      expect(y).toBeLessThanOrEqual(bottom + 0.05);
    }
    expect(ticks[0]).toBe("-3.0");
    expect(ticks.at(-1)).toBe("2.0");
  });

  it("does not invert the axis when every mean is negative", () => {
    const { top, bottom, markYs, ticks } = plotted([
      { date: "2026-09-04", day_type: "weekday", avg_min: -2.0, samples: 1000 },
      { date: "2026-09-05", day_type: "saturday", avg_min: -4.0, samples: 1000 },
    ]);
    for (const y of markYs) {
      expect(y).toBeGreaterThanOrEqual(top - 0.05);
      expect(y).toBeLessThanOrEqual(bottom + 0.05);
    }
    expect(Number(ticks[0])).toBeLessThan(Number(ticks.at(-1)));
  });

  it("leaves the zero-based axis alone when nothing is negative", () => {
    const { ticks } = plotted([
      { date: "2026-09-04", day_type: "weekday", avg_min: 2.0, samples: 1000 },
      { date: "2026-09-05", day_type: "saturday", avg_min: 4.0, samples: 1000 },
    ]);
    expect(ticks).toEqual(["0.0", "2.0", "4.0"]);
  });
});
