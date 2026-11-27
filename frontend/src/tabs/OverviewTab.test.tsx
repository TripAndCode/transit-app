import { describe, it, expect, vi, afterEach } from "vitest";
import { cleanup, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { OverviewTab } from "./OverviewTab";
import * as hooks from "../api/hooks";
import type { OverviewSummary, ReportResponse } from "../api/types";

function lateRoutes(severe: number, route_count: number) {
  return { band_counts: { severe: { count: severe, threshold_min: 5 } }, route_count, min_samples: 100 };
}

function summary(partial: Partial<OverviewSummary> = {}): OverviewSummary {
  return {
    headline: { avg_min: null, baseline_avg_min: null, delta_min: null, delta_pct: null, samples: 0, window_from: "2030-01-01", window_to: "2030-01-07" },
    movers: { worse: [], better: [] },
    concentration: { top_routes: [], rest_share_pct: 0 },
    top_delayed: lateRoutes(0, 0),
    peak_hour: null,
    service_split: [
      { day_type: "weekday", avg_min: null, days: 0, samples: 0, insufficient: true },
      { day_type: "saturday", avg_min: null, days: 0, samples: 0, insufficient: true },
      { day_type: "sunday_holiday", avg_min: null, days: 0, samples: 0, insufficient: true },
    ],
    sparkline_points: [],
    period_from: "2026-05-11",
    period_to: "2026-06-09",
    ...partial,
  };
}

/** The Routes table's first rows, as the preview's request returns them. */
function routeTable(rows: unknown[][] = [["R1", "7.20", "61.0", "30.0", 120, 400, "1.0", false, 2.0]], rows_total = 163): ReportResponse {
  return {
    report_type: "route_table",
    rendered_at: "2026-06-08T00:00:00Z",
    text: "",
    rows,
    ctx: { from: "2026-06-01", to: "2026-06-07", dow: "all", time_band: "all", service: "all", routes: [] },
    definition: { early_departure_sec: 60 },
    scope_applied: {},
    sort: "avg",
    order: "worst",
    rows_total,
    reliable_min_samples: 100,
  } as unknown as ReportResponse;
}

function SearchProbe() {
  const [params] = useSearchParams();
  return <span data-testid="search">{params.toString()}</span>;
}

/** Unless a test asks for a first visit ("fresh") or for whatever an
 *  earlier render stored ("stored"), Pulse's tour has already been seen, so
 *  its dialog stays out of the tests about the screen itself. */
function renderOverview(
  data: OverviewSummary,
  path = "/agencies/8/overview?from=2030-01-01&to=2030-01-07",
  { tour = "seen" }: { tour?: "seen" | "fresh" | "stored" } = {},
) {
  if (tour === "fresh") localStorage.removeItem("transit.pulseTourSeen");
  else if (tour === "seen") localStorage.setItem("transit.pulseTourSeen", "1");
  vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useReport").mockReturnValue({ data: routeTable(), error: null, refetch: vi.fn() } as never);
  // The jump-to-latest-data recovery reads the agency's latest_data_date.
  vi.spyOn(hooks, "useAgencies").mockReturnValue({
    data: [{ agency_id: 8, agency_name: "A", feed_url: "", static_url: null, latest_data_date: "2026-05-01" }],
    isPending: false,
  } as never);
  // A fresh element each time: React skips re-rendering an identical one.
  const tree = () => (
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/agencies/:agencyId/overview" element={<><OverviewTab /><SearchProbe /></>} />
      </Routes>
    </MemoryRouter>
  );
  const { rerender } = renderWithProviders(tree());
  return { rerender: () => rerender(tree()) };
}

describe("OverviewTab", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the empty state when every real signal is empty, even though peak_hour is non-null", () => {
    // peak_hour reads a fixed analyze-period rollup with no date column
    // (agg_route_hour, read by pipeline/reports/overview.py's _peak_hour) — it stays
    // non-null for any date range once an agency has ever had data, so it
    // must NOT count as evidence that THIS range has data.
    renderOverview(
      summary({
        peak_hour: { by_hour: [null, null, 1.2, 2.3], peak_hour: 3, peak_avg_min: 2.3 },
      }),
    );
    expect(screen.getByText("No observations in this range. Try a wider window.")).toBeInTheDocument();
  });

  it("titles the screen with one level-1 heading", () => {
    renderOverview(summary());
    expect(screen.getByRole("heading", { level: 1, name: "Pulse" })).toBeInTheDocument();
  });

  it("puts the scope bar in the heading's column, with its saved views and pin on the heading's row", () => {
    renderOverview(summary());
    const heading = screen.getByRole("heading", { level: 1, name: "Pulse" });
    expect(heading.closest(".ov-page-head")).toContainElement(screen.getByRole("region", { name: "What you're viewing" }));
    expect(heading.closest("header")).toContainElement(screen.getByRole("button", { name: "Show all filters" }));
  });

  it("hides the scope conditions the summary did not use", () => {
    renderOverview(
      { ...summary({}), scope_applied: { from: true, to: true, dow: false, service: false } } as OverviewSummary,
      "/agencies/8/overview?from=2030-01-01&to=2030-01-07&dow=weekday",
    );
    const bar = within(screen.getByRole("region", { name: "What you're viewing" }));
    expect(bar.queryByRole("button", { name: /^Days/ })).toBeNull();
    expect(bar.getByRole("button", { name: /^Period/ })).toBeInTheDocument();
  });

  it("shows real content when the headline has samples", () => {
    renderOverview(summary({ headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" } }));
    expect(screen.queryByText("No observations in this range. Try a wider window.")).not.toBeInTheDocument();
  });

  it("does not render a mini-map — removed as low-value decorative content, spatial exploration stays on the dedicated Map tab", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        top_delayed: lateRoutes(1, 1),
      }),
    );
    expect(screen.getByText(/^Routes to check \(/)).toBeInTheDocument();
    // Asserted on the map container MapLibre itself creates, not on a class
    // name this screen once used: `.ov-map-strip` exists in no stylesheet and
    // no component, so asserting its absence could never fail and guarded
    // nothing. Any map reintroduced here would mount through the app's own
    // MapLibre helper and carry this class whatever the wrapper is called.
    expect(document.querySelector(".maplibregl-map")).not.toBeInTheDocument();
    expect(document.querySelector("canvas")).not.toBeInTheDocument();
  });

  it("renders peak-hour, concentration, and service-split content inline, with nothing to disclose", () => {
    renderOverview(
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        concentration: { top_routes: [{ route_code: "R1", route_short_name: "Line 1", share_pct: 60 }], rest_share_pct: 40 },
        peak_hour: { by_hour: Array(24).fill(1), peak_hour: 17, peak_avg_min: 4.8 },
        service_split: [
          { day_type: "weekday", avg_min: 3.1, days: 5, samples: 4000, insufficient: false },
          { day_type: "saturday", avg_min: null, days: 0, samples: 0, insufficient: true },
          { day_type: "sunday_holiday", avg_min: 2.0, days: 2, samples: 10, insufficient: true },
        ],
      }),
    );
    expect(screen.getByText("Delay concentration")).toBeInTheDocument();
    expect(screen.getByText("Worst hour of day")).toBeInTheDocument();
    expect(screen.getByText("By service day")).toBeInTheDocument();
    // Progressively revealed, not gated behind a disclosure widget.
    expect(document.querySelector("details")).not.toBeInTheDocument();
    expect(document.querySelector("summary")).not.toBeInTheDocument();
  });

  describe("the questions another screen answers in full", () => {
    // Every figure below is counted over the screen's filters on the days the
    // payload names for it, so each link carries exactly that scope.
    const SCOPED = "/agencies/8/overview?from=2026-05-01&to=2026-06-09&dow=weekday&time_band=morning&service=平日&routes=R1,R2"; // i18n-ignore: query contract
    const figures = () =>
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        concentration: { top_routes: [{ route_code: "R1", route_short_name: "Line 1", share_pct: 60 }], rest_share_pct: 40 },
        peak_hour: { by_hour: Array(24).fill(1), peak_hour: 17, peak_avg_min: 4.8 },
        service_split: [
          { day_type: "weekday", avg_min: 3.1, days: 5, samples: 4000, insufficient: false },
          { day_type: "saturday", avg_min: 2.4, days: 2, samples: 900, insufficient: false },
          { day_type: "sunday_holiday", avg_min: 2.0, days: 2, samples: 800, insufficient: false },
        ],
        sparkline_points: [2.1, 2.8, 3.3],
        period_avg_min: 2.7,
      });
    const PERIOD_SCOPE = "from=2026-05-11&to=2026-06-09&dow=weekday&time_band=morning&service=%E5%B9%B3%E6%97%A5&routes=R1%2CR2";

    it("draws the routes to check as the Routes table's own first rows, over the headline's days and the screen's filters", () => {
      renderOverview(figures(), SCOPED);
      const call = vi.mocked(hooks.useReport).mock.calls.find((c) => c[1] === "route_table")!;
      expect(call[0]).toBe(8);
      expect(call[2]).toMatchObject({ from: "2026-06-01", to: "2026-06-07", dow: "weekday", time_band: "morning", service: "平日", routes: ["R1", "R2"] }); // i18n-ignore: query contract
      expect(call[3]).toEqual({ sort: "avg", limit: 5 });
      const anchor = document.querySelector<HTMLElement>(".ov-routes-anchor")!;
      expect(within(anchor).getByRole("table")).toBeInTheDocument();
      expect(within(anchor).getByText("Covers the 163 routes observed 100 or more times.")).toBeInTheDocument();
    });

    it("links the routes to check to the Routes table with the same days, filters and sort", () => {
      renderOverview(figures(), SCOPED);
      expect(screen.getByRole("link", { name: "See all 163 routes →" })).toHaveAttribute(
        "href",
        "/agencies/8/routes?from=2026-06-01&to=2026-06-07&dow=weekday&time_band=morning&service=%E5%B9%B3%E6%97%A5&routes=R1%2CR2&sort=avg",
      );
    });

    it("links the heaviest hour to Time's trend over the period and the screen's filters", () => {
      renderOverview(figures(), SCOPED);
      expect(screen.getByRole("link", { name: "See every hour by day on Time →" })).toHaveAttribute(
        "href",
        `/agencies/8/time?${PERIOD_SCOPE}&report=trend`,
      );
    });

    it("links the sparkline to Time's trend over the period and the screen's filters", () => {
      renderOverview(figures(), SCOPED);
      expect(screen.getByRole("link", { name: "See the trend on Time →" })).toHaveAttribute(
        "href",
        `/agencies/8/time?${PERIOD_SCOPE}&report=trend`,
      );
    });

    it("links the day-type split to Day types & agencies over the period and the screen's filters", () => {
      renderOverview(figures(), SCOPED);
      expect(screen.getByRole("link", { name: "Compare weekdays and weekends/holidays route by route →" })).toHaveAttribute(
        "href",
        `/agencies/8/compare?${PERIOD_SCOPE}&by=periods`,
      );
    });

    it("draws no hour chart or day-type chart of its own", () => {
      renderOverview(figures(), SCOPED);
      for (const title of ["Worst hour of day", "By service day"]) {
        const card = screen.getByText(title).closest(".ov-card")!;
        expect(card.querySelector("svg")).toBeNull();
        expect(within(card as HTMLElement).queryByRole("img")).toBeNull();
      }
    });

    it("counts the hero's late routes out of the same routes the preview says it covers", () => {
      const facts = figures();
      renderOverview({ ...facts, top_delayed: { ...facts.top_delayed, route_count: 163 } }, SCOPED);
      expect(screen.getByRole("button", { name: /Averaging 5 min or more late/ })).toHaveTextContent("/ 163 routes");
      expect(screen.getByText("Covers the 163 routes observed 100 or more times.")).toBeInTheDocument();
    });
  });

  describe("the hero's fact rows", () => {
    const withFacts = (late = lateRoutes(2, 4)) =>
      summary({
        headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.9, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
        concentration: { top_routes: [{ route_code: "R1", route_short_name: "Line 1", share_pct: 60 }], rest_share_pct: 40 },
        peak_hour: { by_hour: Array(24).fill(1), peak_hour: 17, peak_avg_min: 4.8 },
        top_delayed: late,
      });
    const lateRow = () => screen.getByRole("button", { name: /Averaging 5 min or more late/ });

    it("leads from the heaviest-hour row to Time's trend over the period", () => {
      renderOverview(withFacts());
      expect(screen.getByRole("link", { name: /Heaviest hour/ })).toHaveAttribute(
        "href",
        "/agencies/8/time?from=2026-05-11&to=2026-06-09&report=trend",
      );
    });

    it("opens the concentration card's modal from the share row", async () => {
      renderOverview(withFacts());
      await userEvent.click(screen.getByRole("button", { name: /Share of delay in the top route/ }));
      expect(screen.getByRole("dialog", { name: "Delay concentration (top 20)" })).toBeInTheDocument();
    });

    it("prints the late-route count in ink like the facts beside it", () => {
      renderOverview(withFacts());
      expect(within(lateRow()).getByText("2")).toBeInTheDocument();
      expect(lateRow()).toHaveClass("ov-hero-fact", { exact: true });
    });

    it("shows a neutral zero when no route reaches the top band", () => {
      renderOverview(withFacts(lateRoutes(0, 2)));
      expect(within(lateRow()).getByText("0")).toBeInTheDocument();
      expect(lateRow()).toHaveClass("ov-hero-fact", { exact: true });
    });

    // The routes-to-check preview is inline, with no modal of its own: the
    // row takes the reader, and keyboard focus, to it.
    it("brings the routes-to-check preview into view from the late-routes row", async () => {
      const scrollIntoView = vi.fn();
      Element.prototype.scrollIntoView = scrollIntoView;
      try {
        renderOverview(withFacts());
        await userEvent.click(lateRow());
        const list = screen.getByText(/^Routes to check \(/).closest(".ov-routes-anchor");
        expect(list).not.toBeNull();
        expect(scrollIntoView.mock.contexts).toEqual([list]);
        expect(document.activeElement).toBe(list);
        expect(screen.queryByRole("dialog")).toBeNull();
      } finally {
        delete (Element.prototype as { scrollIntoView?: unknown }).scrollIntoView;
      }
    });
  });

  it("offers a clear-routes recovery when the empty result is scoped to specific routes", () => {
    renderOverview(summary(), "/agencies/8/overview?from=2030-01-01&to=2030-01-07&routes=A05");
    expect(screen.getByRole("button", { name: "Clear the route filter" })).toBeInTheDocument();
  });

  it("offers a jump-to-latest-data recovery when the agency has data outside this window", () => {
    renderOverview(summary(), "/agencies/8/overview?from=2030-01-01&to=2030-01-07");
    expect(screen.getByRole("button", { name: "Jump to the latest data" })).toBeInTheDocument();
  });

  it("offers no recoveries beyond the jump when nothing is filtered", () => {
    renderOverview(summary(), "/agencies/8/overview?from=2030-01-01&to=2030-01-07");
    expect(screen.queryByRole("button", { name: "Clear the route filter" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reset service type to all" })).not.toBeInTheDocument();
  });
});

describe("OverviewTab's first-visit tour", () => {
  afterEach(() => vi.restoreAllMocks());

  const withData = () =>
    summary({
      headline: { avg_min: 3.2, baseline_avg_min: 2.8, delta_min: 0.4, delta_pct: 14.3, samples: 50, window_from: "2026-06-01", window_to: "2026-06-07" },
      service_split: [{ day_type: "weekday", avg_min: 3.2, days: 5, samples: 50, insufficient: false }],
    });

  it("opens on a first visit, anchored on the headline figure", async () => {
    renderOverview(withData(), undefined, { tour: "fresh" });
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("What a delay is here")).toBeInTheDocument();
    expect(document.querySelector('[data-tour="pulse-delay"]')).not.toBeNull();
    expect(document.querySelector('[data-tour="pulse-on-time"]')).not.toBeNull();
    expect(document.querySelector('[data-tour="pulse-scope"]')).not.toBeNull();
  });

  it("does not come back once dismissed", async () => {
    renderOverview(withData(), undefined, { tour: "fresh" });
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss this tour" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // Unmounted and opened again, as a later visit to Pulse would.
    cleanup();
    renderOverview(withData(), undefined, { tour: "stored" });
    expect(screen.getByText("3.2")).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("stays put off for the visit after \"Later\", through a refetch that replaces the figures", async () => {
    const { rerender } = renderOverview(withData(), undefined, { tour: "fresh" });
    await userEvent.click(await screen.findByRole("button", { name: "Later" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: undefined, isPending: true, error: null, refetch: vi.fn() } as never);
    rerender();
    expect(document.querySelector('[data-tour="pulse-delay"]')).toBeNull();
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: withData(), isPending: false, error: null, refetch: vi.fn() } as never);
    rerender();
    expect(document.querySelector('[data-tour="pulse-delay"]')).not.toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps its step through the refetch that step 3's scope change starts", async () => {
    const { rerender } = renderOverview(withData(), undefined, { tour: "fresh" });
    await userEvent.click(await screen.findByRole("button", { name: "Next" }));
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("Step 3 of 3")).toBeInTheDocument();
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: undefined, isPending: true, error: null, refetch: vi.fn() } as never);
    rerender();
    expect(screen.queryByRole("dialog")).toBeNull();
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: withData(), isPending: false, error: null, refetch: vi.fn() } as never);
    rerender();
    expect(await screen.findByText("Step 3 of 3")).toBeInTheDocument();
    expect(screen.getByText("What this screen covers")).toBeInTheDocument();
  });

  it("waits for figures: an empty period shows no tour", () => {
    renderOverview(summary(), undefined, { tour: "fresh" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("OverviewTab without a usable agency id", () => {
  it("renders nothing when the route segment is not an agency id", () => {
    renderOverview(summary(), "/agencies/not-an-id/overview?from=2030-01-01&to=2030-01-07");
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.querySelector(".ov-page")).toBeNull();
  });
});

describe("OverviewTab for an agency never collected", () => {
  afterEach(() => vi.restoreAllMocks());

  it("waits for the agency list before saying the range is empty", () => {
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: summary(), isPending: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({ data: undefined, isPending: true } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/8/overview"]}>
        <Routes>
          <Route path="/agencies/:agencyId/overview" element={<OverviewTab />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByText("No observations in this range. Try a wider window.")).not.toBeInTheDocument();
  });

  it("says no data was ever collected and offers another agency", async () => {
    localStorage.setItem("transit.lastAgency", "8");
    vi.spyOn(hooks, "useOverviewSummary").mockReturnValue({ data: summary(), isPending: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useAgencies").mockReturnValue({
      data: [{ agency_id: 8, agency_name: "Hiroden", feed_url: "", static_url: null, latest_data_date: null }],
      isPending: false,
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/8/overview"]}>
        <Routes>
          <Route path="/agencies/:agencyId/overview" element={<OverviewTab />} />
          <Route path="/" element={<p>picker-page</p>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("We haven't collected any data for Hiroden yet.")).toBeInTheDocument();
    expect(screen.queryByText("No observations in this range. Try a wider window.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Choose another agency" }));
    expect(screen.getByText("picker-page")).toBeInTheDocument();
    expect(localStorage.getItem("transit.lastAgency")).toBeNull();
  });
});
