import { afterEach, beforeEach, describe, it, expect, vi } from "vitest";
import { act, cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewHeroRow, type HeroFact } from "./OverviewHeroRow";
import { stubReducedMotion } from "../test/reducedMotion";
import * as hooks from "../api/hooks";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour, OverviewTopDelayedRoute } from "../api/types";
import { formatDateRange } from "../utils/format";

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

function delayedRoute(route_code: string, avg_min: number): OverviewTopDelayedRoute {
  return { route_code, route_short_name: null, avg_min };
}
// Three in the routes-to-check list's top band (5.0 sits on its boundary) and
// one below it.
const topDelayed = [delayedRoute("A", 7.2), delayedRoute("B", 5.0), delayedRoute("C", 6.1), delayedRoute("D", 4.2)];

function mockHooks(routeCount: number, feedAgeHours: number | null) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: Array.from({ length: routeCount }, (_, i) => ({
      route_id: String(i),
      route_code: `R${i}`,
      route_short_name: null,
      route_long_name: null,
      trip_headsigns: [],
    })),
    isPending: false,
  } as never);
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
  mockHooks(38, null);
  vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
    data: { latest_captured_at: capturedAt, date: null, routes: [], raw_samples: 0, clamp_count: 0 },
    isPending: false,
  } as never);
}

function renderHero(overrides: {
  headline?: OverviewHeadline;
  topDelayedRoutes?: OverviewTopDelayedRoute[];
  sparklinePoints?: number[];
  peakHour?: OverviewPeakHour | null;
  concentration?: OverviewConcentration;
  onOpenFact?: (fact: HeroFact) => void;
} = {}) {
  return renderWithProviders(
    <OverviewHeroRow
      headline={overrides.headline ?? headline()}
      topDelayedRoutes={overrides.topDelayedRoutes ?? topDelayed}
      agencyId={1}
      sparklinePoints={overrides.sparklinePoints ?? [2.1, 2.8, 3.3]}
      peakHour={overrides.peakHour === undefined ? peakHour : overrides.peakHour}
      concentration={overrides.concentration ?? concentration}
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

  it("labels the primary figure with the dates it averages", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByText(`Average delay · ${formatDateRange("2026-06-03", "2026-06-09", { year: false })}`)).toBeInTheDocument();
    expect(screen.getByText(/3\.3/)).toBeInTheDocument();
  });

  it("states a worse period as a chip with its delta, then one short sentence", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ delta_min: 0.9 }) });
    expect(screen.getByText("Worse than the period before, +0.9 min")).toBeInTheDocument();
    expect(screen.getByText("Delays are 0.9 min longer than in the 7-day period before.")).toBeInTheDocument();
  });

  it("states a better period as a chip without repeating the delta", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ baseline_avg_min: 3.8, delta_min: -1.2, delta_pct: -13.2 }) });
    expect(screen.getByText("Better than the period before")).toBeInTheDocument();
    expect(screen.getByText("Delays are 1.2 min shorter than in the 7-day period before.")).toBeInTheDocument();
  });

  it("states a period within the noise threshold as about the same", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ delta_min: 0.2 }) });
    expect(screen.getByText("About the same as the period before")).toBeInTheDocument();
    expect(screen.getByText("Delay is about the same as in the 7-day period before.")).toBeInTheDocument();
  });

  // The findings have rows of their own, so the sentence carries the verdict
  // alone and the eye lands on one claim before the numbers.
  it("keeps the peak hour and the concentration share out of the sentence", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({ headline: headline({ delta_min: 0.9 }) });
    expect(container.querySelector(".ov-hero-story")?.textContent).toBe(
      "Delays are 0.9 min longer than in the 7-day period before.",
    );
  });

  it("shows 'no comparison data' and no chip when baseline_avg_min is null", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({
      headline: headline({ baseline_avg_min: null, delta_min: null, delta_pct: null }),
    });
    expect(screen.getByText("No comparison data")).toBeInTheDocument();
    expect(container.querySelector(".ov-hero-state")).toBeNull();
  });

  it("lists the heaviest hour as a fact row that opens the peak-hour card", async () => {
    mockHooks(38, 0.1);
    const onOpenFact = vi.fn();
    renderHero({ onOpenFact });
    const row = screen.getByRole("button", { name: /Heaviest hour/ });
    expect(within(row).getByText("17:00–18:00")).toHaveClass("num");
    await userEvent.click(row);
    expect(onOpenFact).toHaveBeenCalledWith("peak_hour");
  });

  it("lists the top route's share of delay as a fact row that opens the concentration card", async () => {
    mockHooks(38, 0.1);
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
    mockHooks(38, 0.1);
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

  it("lists the routes-to-check list's top-band count, out of the total, with that band's threshold", async () => {
    mockHooks(38, 0.1);
    const onOpenFact = vi.fn();
    renderHero({ onOpenFact });
    const row = screen.getByRole("button", { name: /Averaging over 5 min late/ });
    expect(row).toHaveTextContent("3/ 38 routes");
    await userEvent.click(row);
    expect(onOpenFact).toHaveBeenCalledWith("late_routes");
  });

  it("gives the late-route count, and only it, the warm tone while any route is late", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero();
    const warm = container.querySelectorAll(".ov-hero-fact--attn");
    expect(warm).toHaveLength(1);
    expect(warm[0]).toHaveAccessibleName(/Averaging over 5 min late/);
  });

  it("keeps the late-route count neutral when no route is in the top band", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({ topDelayedRoutes: [delayedRoute("D", 4.9), delayedRoute("E", 3.2)] });
    expect(screen.getByRole("button", { name: /Averaging over 5 min late/ })).toHaveTextContent("0/ 38 routes");
    expect(container.querySelector(".ov-hero-fact--attn")).toBeNull();
  });

  it("drops the total, not the count, while the route list is unknown", () => {
    mockHooks(38, 0.1);
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: undefined, isPending: true } as never);
    renderHero();
    const row = screen.getByRole("button", { name: /Averaging over 5 min late/ });
    expect(row).toHaveTextContent("3routes");
    expect(row).not.toHaveTextContent("/");
  });

  it("omits the heaviest-hour row without an hourly profile, keeping the others", () => {
    mockHooks(38, 0.1);
    renderHero({ peakHour: null });
    expect(screen.queryByRole("button", { name: /Heaviest hour/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Share of delay/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Averaging over 5 min late/ })).toBeInTheDocument();
  });

  it("omits the concentration row without top routes, keeping the others", () => {
    mockHooks(38, 0.1);
    renderHero({ concentration: emptyConcentration });
    expect(screen.queryByRole("button", { name: /Share of delay/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Heaviest hour/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Averaging over 5 min late/ })).toBeInTheDocument();
  });

  it("says how long a quiet feed has been quiet, by the rule Live uses", () => {
    mockHooks(38, 2);
    renderHero();
    expect(screen.getByText("Feed quiet for 2 h")).toBeInTheDocument();
    expect(screen.getByText(/Last report /)).toBeInTheDocument();
  });

  it("renders an inline info hint next to the verdict", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByRole("button", { name: "Hint" })).toBeInTheDocument();
  });

  it("counts a long-quiet feed in days", () => {
    mockHooks(38, 30 * 24);
    renderHero();
    expect(screen.getByText("Feed quiet for 30 days")).toBeInTheDocument();
    // A quiet feed keeps the dot but not the live green.
    expect(document.querySelector(".ov-fresh-dot--stale")).not.toBeNull();
  });

  it("says the feed is reporting live while its newest report is minutes old", () => {
    mockHooks(38, 0.05);
    renderHero();
    expect(screen.getByText("Reporting live")).toBeInTheDocument();
    expect(screen.queryByText(/Feed quiet/)).toBeNull();
  });

  it("claims nothing about reports when the feed status could not be read", () => {
    mockHooks(38, 0.1);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({ data: undefined, error: new Error("500") } as never);
    renderHero();
    expect(screen.queryByText("No reports yet")).toBeNull();
    expect(screen.queryByText("Reporting live")).toBeNull();
  });

  it("claims nothing about a report whose time can't be read", () => {
    mockHooks(38, 0.1);
    vi.spyOn(hooks, "useTodayRouteSummary").mockReturnValue({
      data: { latest_captured_at: "not-a-time", date: null, routes: [], raw_samples: 0, clamp_count: 0 },
    } as never);
    renderHero();
    expect(screen.queryByText("No reports yet")).toBeNull();
    expect(screen.queryByText(/Last report/)).toBeNull();
  });

  it("says so before any report has arrived", () => {
    mockHooks(38, null);
    renderHero();
    expect(screen.getByText("No reports yet")).toBeInTheDocument();
  });

  it("renders a full-bleed trend sparkline when there are at least 2 points", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
  });

  it("renders no sparkline when there are fewer than 2 points", () => {
    mockHooks(38, 0.1);
    renderHero({ sparklinePoints: [3.3] });
    expect(screen.queryByRole("img", { hidden: true })).not.toBeInTheDocument();
  });

  it("prints the hero figure in the text colour, whatever its size", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({ headline: headline({ avg_min: 7.4 }) });
    expect((container.querySelector(".ov-kpi-value") as HTMLElement).style.color).toBe("");
  });

  it("sets the hero delay value and every fact value in the numeral face", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero();
    const value = container.querySelector(".ov-kpi-value");
    expect(value).not.toBeNull();
    expect(value).toHaveClass("num");
    const facts = container.querySelectorAll(".ov-hero-fact-value");
    expect(facts).toHaveLength(3);
    for (const fact of facts) expect(fact).toHaveClass("num");
  });

  it("anchors the sparkline on the period mean, labelled in words", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({ sparklinePoints: [2, 4, 6] });
    // Without a reference the reader cannot tell a half-minute wobble from a
    // ten-minute climb: the shape is auto-scaled to the window's own extremes.
    expect(container.querySelector('[data-testid="sparkline-baseline"]')).not.toBeNull();
    expect(screen.getByText("Selected period's average 4.0 min")).toBeInTheDocument();
  });

  it("drops the reference line when there is no series to average", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero({ sparklinePoints: [] });
    expect(container.querySelector('[data-testid="sparkline-baseline"]')).toBeNull();
    expect(screen.queryByText(/Selected period's average/)).toBeNull();
  });

  it("draws the day-pulse ribbon behind the hero from peak_hour.by_hour", () => {
    mockHooks(38, 0.1);
    renderHero({ peakHour: { by_hour: Array.from({ length: 24 }, (_, h) => h / 4), peak_hour: 8, peak_avg_min: 5.75 } });
    // First child: the two columns after it are positioned, so they paint
    // over it by DOM order.
    expect(document.querySelector(".ov-hero > .ov-pulse-ribbon:first-child")).not.toBeNull();
    expect(document.querySelector(".ov-hero > .ov-hero-text")).not.toBeNull();
  });

  it("draws no ribbon without an hourly profile", () => {
    mockHooks(38, 0.1);
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
    mockHooks(38, null);
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
    mockHooks(38, 0.05);
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
