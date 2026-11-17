import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render as renderPlain, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "../../i18n";
import { createMemoryRouter, RouterProvider, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import type { DefinitionMeta, RevisionRouteRow, RevisionTally, TimetableRevisionRoutes } from "../../api/types";
import { RevisionRoutes } from "./RevisionRoutes";

const DEFINITION: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: null,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: null,
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

const tally = (on_time_pct: number | null, mean_delay_sec: number | null, observations = 150, uncertain = false): RevisionTally => ({
  observations,
  on_time_pct,
  mean_delay_sec,
  uncertain,
});

function route(over: Partial<RevisionRouteRow>): RevisionRouteRow {
  return {
    route_code: "R1",
    route_class: "cut",
    trips_before: 6,
    trips_after: 5,
    last_before: 12 * 3600,
    last_after: 11 * 3600,
    main_stop_id: "S1",
    main_stop_name: "Station",
    longest_gap_before: 3600,
    longest_gap_after: 7200,
    observed_before: tally(0, 400),
    observed_after: tally(100, 30),
    ...over,
  };
}

const OBSERVED: TimetableRevisionRoutes = {
  available: true,
  reason: null,
  day_type: "weekday",
  day_type_known: true,
  weeks_cap: 4,
  capped_by: null,
  full_through: "2026-04-28",
  complete: true,
  window: { weeks: 4, before_first: "2026-03-04", before_last: "2026-03-31", after_first: "2026-04-01", after_last: "2026-04-28" },
  routes: [route({}), route({ route_code: "R2", route_class: "unchanged", observed_before: tally(null, null, 40), observed_after: tally(90, 45, 120, true) })],
  classes: [
    { route_class: "cut", routes: 1, before: tally(0, 400), after: tally(100, 30) },
    { route_class: "added", routes: 0, before: tally(null, null, 0), after: tally(null, null, 0) },
    { route_class: "unchanged", routes: 1, before: tally(80, 60), after: tally(90, 45) },
  ],
  definition: DEFINITION,
};

function render(body: TimetableRevisionRoutes, on = "2026-04-01", { keepCache = false } = {}) {
  const json = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
  const fetchMock = vi.fn((url: string) => {
    if (url.includes("/timetable-revisions/routes")) return Promise.resolve(json(body));
    if (url.endsWith("/routes")) return Promise.resolve(json({ rows: [] }));
    return Promise.resolve(json([]));
  });
  vi.stubGlobal("fetch", fetchMock);
  const router = createMemoryRouter(
    [{ path: "agencies/:agencyId/reports", element: <OnFromUrl fallback={on} /> }],
    { initialEntries: ["/agencies/9/reports?doc=revisions"] },
  );
  if (keepCache) {
    // The shared helper's client drops an unobserved query at once; this one
    // keeps it, as the app's does, so a revision visited before is at hand.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderPlain(
      <QueryClientProvider client={client}>
        <I18nextProvider i18n={i18n}>
          <RouterProvider router={router} />
        </I18nextProvider>
      </QueryClientProvider>,
    );
  } else {
    renderWithProviders(<RouterProvider router={router} />);
  }
  return Object.assign(fetchMock, { router });
}

/** The section for the revision `on` names in the URL, as the page passes it. */
function OnFromUrl({ fallback }: { fallback: string }) {
  const [params] = useSearchParams();
  return <RevisionRoutes agencyId={9} on={params.get("on") ?? fallback} dayType="weekday" />;
}

describe("RevisionRoutes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("compares each route's timetable and how it ran over the same weeks on each side", async () => {
    const fetchMock = render(OBSERVED);
    expect(await screen.findByText(/over the same 4 whole weeks on each side/)).toBeInTheDocument();
    expect(screen.queryByText(/aggregated through/)).not.toBeInTheDocument();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/timetable-revisions/routes?on=2026-04-01&day_type=weekday"))).toBe(true);
    const r1 = screen.getAllByText("Fewer trips").map((cell) => cell.closest("tr")!).find((row) => row.textContent?.includes("Station"))!;
    expect(r1).toHaveTextContent("6 → 5");
    expect(r1).toHaveTextContent("12:00 → 11:00");
    expect(r1).toHaveTextContent("60 min → 120 min");
    expect(r1).toHaveTextContent("0.0% → 100.0%");
    const r2 = screen.getAllByText("Same trips").map((cell) => cell.closest("tr")!).find((row) => row.textContent?.includes("Station"))!;
    expect(r2).toHaveTextContent("— → 90.0% (wide margin)");
    const classes = screen.getByRole("columnheader", { name: "Routes" }).closest("table")!;
    expect(within(classes).getAllByRole("row")).toHaveLength(4);
    expect(screen.getByText(/real-time feed predicted/)).toBeInTheDocument();
  });

  it("says when the observed side appears, before a whole week after the revision is aggregated", async () => {
    render({ ...OBSERVED, complete: false, window: null, classes: [], definition: null, routes: [route({ observed_before: null, observed_after: null })] });
    expect(await screen.findByText(/once a whole week after it is aggregated\. All 4 whole weeks .* through/)).toBeInTheDocument();
    expect(screen.queryByRole("columnheader", { name: "On-time rate" })).not.toBeInTheDocument();
    expect(screen.getByText("Fewer trips")).toBeInTheDocument();
  });

  it("labels a window short of its cap with how many it holds and when the rest arrive", async () => {
    render({ ...OBSERVED, complete: false, window: { ...OBSERVED.window!, weeks: 2 } });
    expect(await screen.findByText(/over 2 of the 4 whole weeks on each side so far.*All 4 are aggregated through/)).toBeInTheDocument();
  });

  it("promises no date past a timetable that caps the comparison, and says why it stops", async () => {
    render({ ...OBSERVED, weeks_cap: 2, capped_by: "timetable", full_through: "2026-04-14", complete: true, window: { ...OBSERVED.window!, weeks: 2 } });
    const status = await screen.findByText(/over the same 2 whole weeks on each side/);
    expect(status).toHaveTextContent("A timetable on one side is in force for under four weeks");
    expect(status).not.toHaveTextContent(/aggregated through/);
  });

  it("says no whole week can be compared when the cap is under a week", async () => {
    render({ ...OBSERVED, weeks_cap: 0, capped_by: "timetable", full_through: null, complete: false, window: null, classes: [], definition: null });
    const status = await screen.findByText(/There is no whole week to compare/);
    expect(screen.queryByText(/once a whole week/)).not.toBeInTheDocument();
    expect(status).not.toHaveTextContent(/no more weeks can be compared/);
  });

  it("names the agency's history when it holds the cap", async () => {
    render({ ...OBSERVED, weeks_cap: 1, capped_by: "history", full_through: "2026-04-07", complete: true, window: { ...OBSERVED.window!, weeks: 1 } });
    expect(await screen.findByText(/aggregated days before the revision hold under four weeks/)).toBeInTheDocument();
  });

  it("lists fifty routes and offers the rest", async () => {
    const many = Array.from({ length: 51 }, (_, i) => route({ route_code: `X${i}`, main_stop_name: `Stop ${i}` }));
    render({ ...OBSERVED, routes: many });
    await screen.findByText(/Stop 0/);
    expect(screen.queryByText(/Stop 50/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show all 51 routes" }));
    expect(screen.getByText(/Stop 50/)).toBeInTheDocument();
  });

  it("lists fifty again for another revision after the reader showed them all", async () => {
    const many = Array.from({ length: 51 }, (_, i) => route({ route_code: `X${i}`, main_stop_name: `Stop ${i}` }));
    const { router } = render({ ...OBSERVED, routes: many }, "2026-04-01", { keepCache: true });
    await screen.findByText(/Stop 0/);
    await act(() => router.navigate("/agencies/9/reports?doc=revisions&on=2026-10-01"));
    await screen.findByText(/Stop 0/);
    fireEvent.click(screen.getByRole("button", { name: "Show all 51 routes" }));
    expect(screen.getByText(/Stop 50/)).toBeInTheDocument();
    // Back to a revision already fetched: its data is at hand, so the section never unmounts.
    await act(() => router.navigate("/agencies/9/reports?doc=revisions&on=2026-04-01"));
    await waitFor(() => expect(screen.queryByText(/Stop 50/)).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Show all 51 routes" })).toBeInTheDocument();
  });

  it("leaves an unknown day type to the stop comparison's explanation", async () => {
    render({ ...OBSERVED, day_type_known: false, routes: [] });
    const heading = await screen.findByRole("heading", { name: "Routes" });
    // The response has arrived once the section holds its (empty) body.
    await waitFor(() => expect(heading.parentElement!.querySelector("[aria-busy]")).not.toBeNull());
    expect(heading.parentElement!.querySelector("[aria-busy]")).toBeEmptyDOMElement();
    expect(screen.queryByText(/has no day of that type in force/)).not.toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says no route runs on a day type neither timetable serves", async () => {
    render({ ...OBSERVED, routes: [] });
    expect(await screen.findByText("No route runs on this day type under either timetable.")).toBeInTheDocument();
  });

  it("shows nothing of its own when the revision cannot be compared", async () => {
    render({ ...OBSERVED, available: false, reason: "no_calendar", routes: [] });
    expect(await screen.findByRole("heading", { name: "Routes" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});
