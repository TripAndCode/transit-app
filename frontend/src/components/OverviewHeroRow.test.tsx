import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewHeroRow } from "./OverviewHeroRow";
import * as hooks from "../api/hooks";
import type { OverviewConcentration, OverviewHeadline, OverviewPeakHour } from "../api/types";

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

function renderHero(overrides: {
  headline?: OverviewHeadline;
  delayedCount?: number;
  sparklinePoints?: number[];
  peakHour?: OverviewPeakHour | null;
  concentration?: OverviewConcentration;
} = {}) {
  return renderWithProviders(
    <OverviewHeroRow
      headline={overrides.headline ?? headline()}
      delayedCount={overrides.delayedCount ?? 3}
      agencyId={1}
      sparklinePoints={overrides.sparklinePoints ?? [2.1, 2.8, 3.3]}
      peakHour={overrides.peakHour === undefined ? peakHour : overrides.peakHour}
      concentration={overrides.concentration ?? concentration}
    />,
  );
}

describe("OverviewHeroRow", () => {
  it("renders the eyebrow label and the primary avg-delay value", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByText("Network avg delay")).toBeInTheDocument();
    expect(screen.getByText(/3\.3/)).toBeInTheDocument();
  });

  it("renders the delayed-route count over the total from useRoutes", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByText("3 / 38 routes")).toBeInTheDocument();
  });

  it("renders the behind-schedule story sentence with the delta, peak hour, and concentration share", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ delta_min: 0.9 }) });
    expect(
      screen.getByText(/Running 0\.9 min behind last week; the 17:00 hour is heaviest\. 60% of delay sits in the top 1 route\./),
    ).toBeInTheDocument();
  });

  // One dominant route is a real case, not an edge one: a small agency may
  // only have a single observed route. Both halves of the plural go through
  // i18next here, where the fake `t` in storySentence.test.ts cannot see them.
  it("pluralises the concentration clause on the number of top routes", () => {
    mockHooks(38, 0.1);
    renderHero({
      headline: headline({ delta_min: 0.9 }),
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
    expect(screen.getByText(/60% of delay sits in the top 3 routes\./)).toBeInTheDocument();
  });

  it("renders the ahead-of-schedule story sentence for a negative delta", () => {
    mockHooks(38, 0.1);
    renderHero({
      headline: headline({ baseline_avg_min: 3.8, delta_min: -1.2, delta_pct: -13.2 }),
    });
    expect(screen.getByText(/Running 1\.2 min ahead of last week/)).toBeInTheDocument();
  });

  it("falls back to the short story template when peak-hour data is missing", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ delta_min: 0.9 }), peakHour: null });
    expect(screen.getByText("Running 0.9 min behind last week.")).toBeInTheDocument();
  });

  it("falls back to the short story template when concentration has no top routes", () => {
    mockHooks(38, 0.1);
    renderHero({ headline: headline({ delta_min: 0.9 }), concentration: emptyConcentration });
    expect(screen.getByText("Running 0.9 min behind last week.")).toBeInTheDocument();
  });

  it("shows 'no comparison data' instead of a story sentence when baseline_avg_min is null", () => {
    mockHooks(38, 0.1);
    renderHero({
      headline: headline({ baseline_avg_min: null, delta_min: null, delta_pct: null }),
    });
    expect(screen.getByText("No comparison data")).toBeInTheDocument();
  });

  it("shows the feed's last-updated age", () => {
    mockHooks(38, 2);
    renderHero();
    expect(screen.getByText(/Last updated/)).toBeInTheDocument();
  });

  it("renders an inline info hint next to the story sentence", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByRole("button", { name: "Hint" })).toBeInTheDocument();
  });

  it("shows a stale-feed label instead of 'Running normally' when the feed is stale", () => {
    mockHooks(38, 30 * 24); // 30 days old — well past the 24h threshold
    renderHero();
    expect(screen.getByText("Data delayed")).toBeInTheDocument();
    expect(screen.queryByText("Running normally")).not.toBeInTheDocument();
  });

  it("keeps 'Running normally' when the feed is fresh", () => {
    mockHooks(38, 0.1);
    renderHero();
    expect(screen.getByText("Running normally")).toBeInTheDocument();
    expect(screen.queryByText("Data delayed")).not.toBeInTheDocument();
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

  it("keeps the hero delay value on proportional figures, not tabular-nums", () => {
    mockHooks(38, 0.1);
    const { container } = renderHero();
    const value = container.querySelector(".ov-kpi-value");
    expect(value).not.toBeNull();
    expect(value!.className.split(/\s+/)).not.toContain("num");
    expect(value!.getAttribute("style") ?? "").not.toMatch(/tabular-nums/);
  });
});
