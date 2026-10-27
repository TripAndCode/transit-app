import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewHeroRow, type HeroFact } from "./OverviewHeroRow";
import { stubReducedMotion } from "../test/reducedMotion";
import * as hooks from "../api/hooks";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour, OverviewServiceSplitDay } from "../api/types";
import { formatDate, formatDateRange } from "../utils/format";

function headline(partial: Partial<OverviewHeadline> = {}): OverviewHeadline {
  return {
    avg_min: 3.3,
    baseline_avg_min: 2.8,
    delta_min: 0.5,
    delta_pct: 17.9,
    samples: 500,
    window_from: "2026-06-03",
    window_to: "2026-06-09",
    ...partial,
  };
}

const peakHour: OverviewPeakHour = { by_hour: [], peak_hour: 17, peak_avg_min: 4.8 };
const concentration: OverviewConcentration = {
  top_routes: [{ route_code: "42", route_short_name: null, share_pct: 60 }],
  rest_share_pct: 40,
};
const emptyConcentration: OverviewConcentration = { top_routes: [], rest_share_pct: 0 };

function mockFeed(feedAgeHours: number | null) {
  const capturedAt =
    feedAgeHours == null ? null : new Date(Date.now() - feedAgeHours * 3600_000).toISOString();
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
    data: { latest_captured_at: capturedAt, date: null, routes: [], raw_samples: 0, clamp_count: 0 },
    isPending: false,
  } as never);
}

// The feed's latest report at an exact instant, for a test that pins the
// clock rather than offsetting from the real one.
function mockFeedSummary(capturedAt: string | null) {
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
    data: { latest_captured_at: capturedAt, date: null, routes: [], raw_samples: 0, clamp_count: 0 },
    isPending: false,
  } as never);
}

function renderHero(overrides: {
  headline?: OverviewHeadline;
  severeCount?: number;
  sparklinePoints?: number[];
  periodAvgMin?: number | null;
  peakHour?: OverviewPeakHour | null;
  concentration?: OverviewConcentration;
  routeCount?: number;
  daily?: OverviewServiceSplitDay[];
  onOpenFact?: (fact: HeroFact) => void;
} = {}) {
  return renderWithProviders(
    <OverviewHeroRow
      headline={overrides.headline ?? headline()}
      severeCount={overrides.severeCount ?? 3}
      severeThresholdMin={5}
      routeCount={overrides.routeCount ?? 38}
      minSamples={100}
      agencyId={1}
      sparklinePoints={overrides.sparklinePoints ?? [2.1, 2.8, 3.3]}
      periodAvgMin={overrides.periodAvgMin === undefined ? 2.7 : overrides.periodAvgMin}
      headlineRange={formatDateRange("2026-06-03", "2026-06-09", { year: false })}
      periodRange={formatDateRange("2026-05-11", "2026-06-09", { year: false })}
      peakHour={overrides.peakHour === undefined ? peakHour : overrides.peakHour}
      concentration={overrides.concentration ?? concentration}
      daily={overrides.daily ?? []}
      onOpenFact={overrides.onOpenFact ?? (() => {})}
    />,
  );
}

describe("OverviewHeroRow", () => {
  beforeEach(() => {
    stubReducedMotion();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("labels the primary figure with how many days it averages, and which", () => {
    mockFeed(0.1);
    renderHero();
    const week = formatDateRange("2026-06-03", "2026-06-09", { year: false });
    expect(screen.getByText(`Average delay, last 7 days (${week})`)).toBeInTheDocument();
    expect(document.querySelector(".ov-kpi-value")).toHaveTextContent(/^3\.3/);
  });

  it("states a longer window as a chip with its change, then both averages and the change in a sentence", () => {
    mockFeed(0.1);
    renderHero({ headline: headline({ avg_min: 3.23, baseline_avg_min: 2.91, delta_min: 0.32, delta_pct: 11 }) });
    expect(screen.getByText("+0.3 min on the 7 days before")).toBeInTheDocument();
    expect(
      screen.getByText("The last 7 days averaged 3.2 min, 0.3 min (11%) longer than the 7 days before (2.9 min)."),
    ).toBeInTheDocument();
  });

  it("states a shorter window the same way", () => {
    mockFeed(0.1);
    renderHero({ headline: headline({ avg_min: 2.6, baseline_avg_min: 3.8, delta_min: -1.2, delta_pct: -31.6 }) });
    expect(screen.getByText("\u22121.2 min on the 7 days before")).toBeInTheDocument();
    expect(
      screen.getByText("The last 7 days averaged 2.6 min, 1.2 min (32%) shorter than the 7 days before (3.8 min)."),
    ).toBeInTheDocument();
  });

  it("calls a window about the same only within the bounds, still with both averages", () => {
    mockFeed(0.1);
    renderHero({ headline: headline({ avg_min: 2.52, baseline_avg_min: 2.46, delta_min: 0.06, delta_pct: 2.4 }) });
    expect(screen.getByText("About the same as the 7 days before")).toBeInTheDocument();
    expect(
      screen.getByText("The last 7 days averaged 2.5 min, about the same as the 7 days before (2.5 min)."),
    ).toBeInTheDocument();
  });

  it("notes the latest day when it is among the period's slowest, by its date and the days observed", () => {
    mockFeed(0.1);
    // 2026-06-09, the headline's last day, is the second slowest of eight.
    const daily: OverviewServiceSplitDay[] = Array.from({ length: 8 }, (_, i) => ({
      date: `2026-06-0${i + 2}`,
      day_type: "weekday",
      avg_min: i === 7 ? 5.02 : i === 0 ? 5.32 : 3,
      samples: 1000,
    }));
    const { container } = renderHero({ daily });
    const date = formatDate("2026-06-09", { year: false, weekday: true });
    const period = formatDateRange("2026-05-11", "2026-06-09", { year: false });
    expect(container.querySelector(".ov-hero-note")).toHaveTextContent(
      `The latest day, ${date}, averaged 5.0 min: the second slowest of the 8 days observed in ${period}.`,
    );
  });

  it("adds no note when the latest day is not among the slowest", () => {
    mockFeed(0.1);
    const daily: OverviewServiceSplitDay[] = Array.from({ length: 8 }, (_, i) => ({
      date: `2026-06-0${i + 2}`,
      day_type: "weekday",
      avg_min: i === 7 ? 2 : 3,
      samples: 1000,
    }));
    const { container } = renderHero({ daily });
    expect(container.querySelector(".ov-hero-note")).toBeNull();
  });

  // The findings have rows of their own, so the sentence carries the verdict
  // alone and the eye lands on one claim before the numbers.
  it("keeps the peak hour and the concentration share out of the sentence", () => {
    mockFeed(0.1);
    const { container } = renderHero({ headline: headline({ avg_min: 3.7, delta_min: 0.9, delta_pct: 32.1 }) });
    expect(container.querySelector(".ov-hero-story")?.textContent).toBe(
      "The last 7 days averaged 3.7 min, 0.9 min (32%) longer than the 7 days before (2.8 min).",
    );
  });

  it("shows 'no comparison data' and no chip when baseline_avg_min is null", () => {
    mockFeed(0.1);
    const { container } = renderHero({
      headline: headline({ baseline_avg_min: null, delta_min: null, delta_pct: null }),
    });
    expect(screen.getByText("No comparison data")).toBeInTheDocument();
    expect(container.querySelector(".ov-hero-state")).toBeNull();
  });

  it("dates each fact by the days it counts", () => {
    mockFeed(0.1);
    renderHero();
    // The heaviest hour and the delay share count the whole period; the
    // late-route count, like the headline, counts the headline's window.
    const period = formatDateRange("2026-05-11", "2026-06-09", { year: false });
    const week = formatDateRange("2026-06-03", "2026-06-09", { year: false });
    expect(screen.getByRole("button", { name: /Heaviest hour/ })).toHaveTextContent(period);
    expect(screen.getByRole("button", { name: /Share of delay/ })).toHaveTextContent(period);
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toHaveTextContent(
      `Observed 100+ times, ${week}`,
    );
  });

  it("lists the heaviest hour as a fact row that opens the peak-hour card", async () => {
    mockFeed(0.1);
    const onOpenFact = vi.fn();
    renderHero({ onOpenFact });
    const row = screen.getByRole("button", { name: /Heaviest hour/ });
    expect(within(row).getByText("17:00–18:00")).toHaveClass("num");
    await userEvent.click(row);
    expect(onOpenFact).toHaveBeenCalledWith("peak_hour");
  });

  it("lists the top route's share of delay as a fact row that opens the concentration card", async () => {
    mockFeed(0.1);
    const onOpenFact = vi.fn();
    renderHero({ onOpenFact });
    const row = screen.getByRole("button", { name: /Share of delay in the top route/ });
    expect(row).toHaveTextContent("60%");
    await userEvent.click(row);
    expect(onOpenFact).toHaveBeenCalledWith("concentration");
  });

  // One dominant route is a real case, not an edge one: a small agency may
  // only have a single observed route. Both halves of the plural go through
  // i18next here, where the fake `t` in storySentence.test.ts cannot see them.
  it("pluralises the concentration label on the number of top routes", () => {
    mockFeed(0.1);
    renderHero({
      concentration: {
        top_routes: [
          { route_code: "42", route_short_name: null, share_pct: 30 },
          { route_code: "27", route_short_name: null, share_pct: 20 },
          { route_code: "15", route_short_name: null, share_pct: 10 },
        ],
        rest_share_pct: 40,
        rest_route_count: 5,
      },
    });
    expect(screen.getByRole("button", { name: /Share of delay in the top 3 routes/ })).toHaveTextContent("60%");
  });

  it("lists the count of routes over the severe threshold, out of the total, with that threshold", async () => {
    mockFeed(0.1);
    const onOpenFact = vi.fn();
    renderHero({ onOpenFact });
    const row = screen.getByRole("button", { name: /Averaging 5 min or more late/ });
    expect(row).toHaveTextContent("3/ 38 routes");
    await userEvent.click(row);
    expect(onOpenFact).toHaveBeenCalledWith("late_routes");
  });

  it("gives the late-route count, and only it, the warm tone while any route is late", () => {
    mockFeed(0.1);
    const { container } = renderHero();
    const warm = container.querySelectorAll(".ov-hero-fact--attn");
    expect(warm).toHaveLength(1);
    expect(warm[0]).toHaveAccessibleName(/Averaging 5 min or more late/);
  });

  it("keeps the late-route count neutral when no route is in the top band", () => {
    mockFeed(0.1);
    const { container } = renderHero({ severeCount: 0 });
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toHaveTextContent("0/ 38 routes");
    expect(container.querySelector(".ov-hero-fact--attn")).toBeNull();
  });

  // The routes-to-check list holds five routes; the count is the server's,
  // over every route.
  it("prints a count past the five routes the list holds", () => {
    mockFeed(0.1);
    renderHero({ severeCount: 12 });
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toHaveTextContent("12/ 38 routes");
  });

  // Out of the routes the list ranks, not the timetable's: the count and its
  // total are one population.
  it("counts out of the routes ranked in the window, whatever the timetable holds", () => {
    mockFeed(0.1);
    renderHero({ severeCount: 33, routeCount: 163 });
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toHaveTextContent("33/ 163 routes");
  });

  it("omits the heaviest-hour row without an hourly profile, keeping the others", () => {
    mockFeed(0.1);
    renderHero({ peakHour: null });
    expect(screen.queryByRole("button", { name: /Heaviest hour/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Share of delay/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toBeInTheDocument();
  });

  it("omits the concentration row without top routes, keeping the others", () => {
    mockFeed(0.1);
    renderHero({ concentration: emptyConcentration });
    expect(screen.queryByRole("button", { name: /Share of delay/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Heaviest hour/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toBeInTheDocument();
  });

  it("says how long a quiet feed has been quiet, by the rule Live uses", () => {
    mockFeed(2);
    renderHero();
    expect(screen.getByText("Feed quiet for 2 h")).toBeInTheDocument();
    expect(screen.getByText(/Last report /)).toBeInTheDocument();
  });

  it("renders an inline info hint next to the verdict", () => {
    mockFeed(0.1);
    renderHero();
    expect(screen.getByRole("button", { name: "Hint" })).toBeInTheDocument();
  });

  it("counts a long-quiet feed in days", () => {
    mockFeed(30 * 24);
    renderHero();
    expect(screen.getByText("Feed quiet for 30 days")).toBeInTheDocument();
    // A quiet feed keeps the dot but not the live green.
    expect(document.querySelector(".ov-fresh-dot--stale")).not.toBeNull();
  });

  it("says the feed is reporting live while its newest report is minutes old", () => {
    mockFeed(0.05);
    renderHero();
    expect(screen.getByText("Reporting live")).toBeInTheDocument();
    expect(screen.queryByText(/Feed quiet/)).toBeNull();
  });

  it("claims nothing about reports when the feed status could not be read", () => {
    mockFeed(0.1);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: undefined, error: new Error("500") } as never);
    renderHero();
    expect(screen.queryByText("No reports yet")).toBeNull();
    expect(screen.queryByText("Reporting live")).toBeNull();
  });

  it("claims nothing about a report whose time can't be read", () => {
    mockFeed(0.1);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "not-a-time", date: null, routes: [], raw_samples: 0, clamp_count: 0 },
    } as never);
    renderHero();
    expect(screen.queryByText("No reports yet")).toBeNull();
    expect(screen.queryByText(/Last report/)).toBeNull();
  });

  it("says so before any report has arrived", () => {
    mockFeed(null);
    renderHero();
    expect(screen.getByText("No reports yet")).toBeInTheDocument();
  });

  it("renders a full-bleed trend sparkline when there are at least 2 points", () => {
    mockFeed(0.1);
    renderHero();
    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
  });

  it("renders no sparkline when there are fewer than 2 points", () => {
    mockFeed(0.1);
    renderHero({ sparklinePoints: [3.3] });
    expect(screen.queryByRole("img", { hidden: true })).not.toBeInTheDocument();
  });

  it("prints the hero figure in the text colour, whatever its size", () => {
    mockFeed(0.1);
    const { container } = renderHero({ headline: headline({ avg_min: 7.4 }) });
    expect((container.querySelector(".ov-kpi-value") as HTMLElement).style.color).toBe("");
  });

  it("sets the hero delay value and every fact value in the numeral face", () => {
    mockFeed(0.1);
    const { container } = renderHero();
    const value = container.querySelector(".ov-kpi-value");
    expect(value).not.toBeNull();
    expect(value).toHaveClass("num");
    const facts = container.querySelectorAll(".ov-hero-fact-value");
    expect(facts).toHaveLength(3);
    for (const fact of facts) expect(fact).toHaveClass("num");
  });

  it("anchors the sparkline on the period's sample-weighted mean, labelled in words", () => {
    mockFeed(0.1);
    // The line is the API's period_avg_min, not a mean of the points: [1, 5]
    // would average to 3.0. The weighting itself is the backend's, tested in
    // tests/api/test_overview.py.
    const { container } = renderHero({ sparklinePoints: [1, 5], periodAvgMin: 4.6 });
    // Without a reference the reader cannot tell a half-minute wobble from a
    // ten-minute climb: the shape is auto-scaled to the window's own extremes.
    expect(container.querySelector('[data-testid="sparkline-baseline"]')).not.toBeNull();
    const period = formatDateRange("2026-05-11", "2026-06-09", { year: false });
    expect(screen.getByText(`${period} average 4.6 min`)).toBeInTheDocument();
  });

  it("drops the reference line when the sparkline has too few points to draw", () => {
    mockFeed(0.1);
    for (const sparklinePoints of [[], [4]]) {
      const { container, unmount } = renderHero({ sparklinePoints, periodAvgMin: 4 });
      expect(container.querySelector('[data-testid="sparkline-baseline"]')).toBeNull();
      expect(screen.queryByText(/ average /)).toBeNull();
      unmount();
    }
  });

  it("draws the day-pulse ribbon inside the figure column, never behind the facts", () => {
    mockFeed(0.1);
    renderHero({ peakHour: { by_hour: Array.from({ length: 24 }, (_, h) => h / 4), peak_hour: 8, peak_avg_min: 5.75 } });
    expect(document.querySelector(".ov-hero-figure > .ov-pulse-ribbon")).not.toBeNull();
    // The figure's text follows both charts in DOM order, so it paints over them.
    const figure = [...(document.querySelector(".ov-hero-figure")?.children ?? [])];
    expect(figure.findIndex((el) => el.matches(".ov-pulse-ribbon"))).toBeLessThan(
      figure.findIndex((el) => el.matches(".ov-hero-label")),
    );
    expect(document.querySelector(".ov-hero-text svg")).toBeNull();
  });

  it("draws no ribbon without an hourly profile", () => {
    mockFeed(0.1);
    renderHero({ peakHour: null });
    expect(document.querySelector(".ov-pulse-ribbon")).toBeNull();
  });

  it("breathes only while the latest report is under two minutes old", () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-03T08:00:00Z") });
    mockFeedSummary("2026-10-03T07:59:00Z");
    renderHero();
    expect(document.querySelector(".ov-fresh-dot--live")).not.toBeNull();
    cleanup();
    mockFeedSummary("2026-10-03T07:55:00Z");
    renderHero();
    expect(screen.getByText("Reporting live")).toBeInTheDocument();
    expect(document.querySelector(".ov-fresh-dot")).not.toBeNull();
    expect(document.querySelector(".ov-fresh-dot--live")).toBeNull();
    expect(document.querySelector(".ov-fresh-dot--stale")).toBeNull();
  });

  it("claims no live green for a feed that has never reported, and no dot at all before the status is read", () => {
    mockFeedSummary(null);
    renderHero();
    expect(screen.getByText("No reports yet")).toBeInTheDocument();
    expect(document.querySelector(".ov-fresh-dot--stale")).not.toBeNull();
    cleanup();
    mockFeed(null);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: undefined, isPending: true } as never);
    renderHero();
    expect(document.querySelector(".ov-fresh-dot")).toBeNull();
  });

  it("drops the live green when the live window closes, without a refetch", () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-03T08:00:00Z") });
    mockFeedSummary("2026-10-03T07:55:00Z");
    renderHero();
    expect(screen.getByText("Reporting live")).toBeInTheDocument();
    expect(document.querySelector(".ov-fresh-dot--stale")).toBeNull();
    act(() => {
      vi.advanceTimersByTime(5 * 60_000 + 1_000);
    });
    expect(document.querySelector(".ov-fresh-dot--stale")).not.toBeNull();
    expect(screen.queryByText("Reporting live")).toBeNull();
  });

  it("keeps the feed status under the figure", () => {
    mockFeed(0.05);
    const { container } = renderHero();
    expect(container.querySelector(".ov-hero-figure .ov-hero-feed")).toHaveTextContent(/Reporting live/);
  });

  it("stops breathing when the two-minute window closes, without a refetch", () => {
    vi.useFakeTimers({ now: Date.parse("2026-10-03T08:00:00Z") });
    mockFeedSummary("2026-10-03T07:59:00Z");
    renderHero();
    expect(document.querySelector(".ov-fresh-dot--live")).not.toBeNull();
    act(() => {
      vi.advanceTimersByTime(60_000);
    });
    expect(document.querySelector(".ov-fresh-dot--live")).toBeNull();
  });
});
