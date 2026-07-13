import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewHeroRow } from "./OverviewHeroRow";
import * as hooks from "../api/hooks";
import type { OverviewHeadline } from "../api/types";

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

describe("OverviewHeroRow", () => {
  it("renders the network avg delay with a positive delta", () => {
    mockHooks(38, 0.1);
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByText("Network avg delay")).toBeInTheDocument();
    expect(screen.getByText(/3\.3/)).toBeInTheDocument();
    expect(screen.getByText(/\+0\.5 min vs\. last week/)).toBeInTheDocument();
  });

  it("renders the delayed-route count over the total from useRoutes", () => {
    mockHooks(38, 0.1);
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByText("3 / 38 routes")).toBeInTheDocument();
  });

  it("renders a negative delta with a minus sign, not indistinguishable from a positive one", () => {
    mockHooks(38, 0.1);
    renderWithProviders(
      <OverviewHeroRow
        headline={headline({ baseline_avg_min: 3.8, delta_min: -0.5, delta_pct: -13.2 })}
        delayedCount={3}
        agencyId={1}
        sparklinePoints={[2.1, 2.8, 3.3]}
      />,
    );
    expect(screen.getByText(/-0\.5 min vs\. last week/)).toBeInTheDocument();
    expect(screen.queryByText(/\+0\.5 min vs\. last week/)).not.toBeInTheDocument();
  });

  it("shows 'no comparison data' when baseline_avg_min is null", () => {
    mockHooks(38, 0.1);
    renderWithProviders(
      <OverviewHeroRow
        headline={headline({ baseline_avg_min: null, delta_min: null, delta_pct: null })}
        delayedCount={3}
        agencyId={1}
        sparklinePoints={[2.1, 2.8, 3.3]}
      />,
    );
    expect(screen.getByText("No comparison data")).toBeInTheDocument();
  });

  it("shows the feed's last-updated age", () => {
    mockHooks(38, 2);
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByText(/Last updated/)).toBeInTheDocument();
  });

  it("renders an inline info hint next to the baseline comparison", () => {
    mockHooks(38, 0.1);
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByRole("button", { name: "Hint" })).toBeInTheDocument();
  });

  it("shows a stale-feed label instead of 'Running normally' when the feed is stale", () => {
    mockHooks(38, 30 * 24); // 30 days old — well past the 24h threshold
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByText("Data delayed")).toBeInTheDocument();
    expect(screen.queryByText("Running normally")).not.toBeInTheDocument();
  });

  it("keeps 'Running normally' when the feed is fresh", () => {
    mockHooks(38, 0.1);
    renderWithProviders(<OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />);
    expect(screen.getByText("Running normally")).toBeInTheDocument();
    expect(screen.queryByText("Data delayed")).not.toBeInTheDocument();
  });

  it("renders a trend sparkline when there are at least 2 points", () => {
    mockHooks(38, 0.1);
    renderWithProviders(
      <OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[2.1, 2.8, 3.3]} />,
    );
    expect(screen.getByRole("img", { hidden: true })).toBeInTheDocument();
  });

  it("renders no sparkline when there are fewer than 2 points", () => {
    mockHooks(38, 0.1);
    renderWithProviders(
      <OverviewHeroRow headline={headline()} delayedCount={3} agencyId={1} sparklinePoints={[3.3]} />,
    );
    expect(screen.queryByRole("img", { hidden: true })).not.toBeInTheDocument();
  });
});
