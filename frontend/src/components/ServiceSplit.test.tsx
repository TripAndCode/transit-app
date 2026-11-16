import { afterEach, describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import type { OverviewDayType, OverviewServiceSplitEntry } from "../api/types";
import { ServiceSplit } from "./ServiceSplit";

type Figures = [avg_min: number | null, days: number, samples: number, insufficient?: boolean];

function entry(day_type: OverviewDayType, [avg_min, days, samples, insufficient = samples === 0]: Figures) {
  return { day_type, avg_min, days, samples, insufficient };
}

function split(weekday: Figures, saturday: Figures, sunday_holiday: Figures): OverviewServiceSplitEntry[] {
  return [entry("weekday", weekday), entry("saturday", saturday), entry("sunday_holiday", sunday_holiday)];
}

const TYPICAL = split([3.2, 18, 1_712_345], [3.0, 4, 234_567], [2.0, 4, 230_001]);
const COMPARE_HREF = "/agencies/1/compare?from=2026-05-11&to=2026-06-09&by=periods";

function renderSplit(service_split: OverviewServiceSplitEntry[]) {
  return renderWithProviders(
    <MemoryRouter>
      <ServiceSplit service_split={service_split} compareHref={COMPARE_HREF} />
    </MemoryRouter>,
  );
}

describe("ServiceSplit card", () => {
  afterEach(async () => {
    await i18n.changeLanguage("en");
  });

  it("compares the slowest day type with the fastest", () => {
    renderSplit(TYPICAL);
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("draws no chart of its own: weekday against weekend is drawn on Day types & agencies", () => {
    const { container } = renderSplit(TYPICAL);
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.getByRole("link", { name: "Compare weekdays and weekends/holidays route by route →" })).toHaveAttribute(
      "href",
      COMPARE_HREF,
    );
  });

  it("names both day types that tie for slowest at the shown precision", () => {
    renderSplit(split([3.24, 18, 100], [3.16, 4, 100], [2.0, 4, 100]));
    expect(
      screen.getByText("Weekdays and Saturdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("names both day types that tie for fastest", () => {
    renderSplit(split([3.2, 18, 100], [2.0, 4, 100], [2.0, 4, 100]));
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Saturdays and Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("says there is no difference when the shown means are equal", () => {
    renderSplit(split([2.04, 18, 100], [1.96, 4, 100], [2.0, 4, 100]));
    expect(screen.getByText("Delay is about the same whatever the day type.")).toBeInTheDocument();
  });

  it("prints each day type's mean, or why it has none, under the sentence", () => {
    renderSplit(split([3.2, 18, 5000], [6.0, 1, 900, true], [null, 0, 0]));
    expect(screen.getByText("Weekdays 3.2 min · Saturdays not enough data · Sundays & holidays no data")).toBeInTheDocument();
  });

  it("compares only the day types with data", () => {
    renderSplit(split([3.2, 18, 100], [null, 0, 0], [2.0, 4, 100]));
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("splits a feed with one weekend timetable into weekdays and weekends", () => {
    renderSplit([entry("weekday", [3.0, 18, 5000]), entry("weekend_holiday", [2.5, 8, 2000])]);
    expect(
      screen.getByText("Weekdays run the latest, 0.5 min longer on average than Weekends & holidays."),
    ).toBeInTheDocument();
  });

  it("leaves a day type with too little data out of the comparison", () => {
    renderSplit(split([3.2, 18, 5000], [6.0, 1, 900, true], [2.0, 4, 2000]));
    expect(
      screen.getByText("Weekdays run the latest, 1.2 min longer on average than Sundays & holidays."),
    ).toBeInTheDocument();
  });

  it("says so when fewer than two day types have enough data", () => {
    renderSplit(split([3.2, 18, 5000], [3.0, 2, 800, true], [null, 0, 0]));
    expect(screen.queryByText(/run the latest|about the same/)).toBeNull();
    expect(screen.getByText("Too little data in this period to compare day types.")).toBeInTheDocument();
  });

  it("reads in Japanese", async () => {
    await i18n.changeLanguage("ja");
    renderSplit(TYPICAL);
    expect(screen.getByText("平日が最も遅れ、日祝より平均1.2分長い。")).toBeInTheDocument(); // i18n-ignore: Japanese copy under test
  });
});
