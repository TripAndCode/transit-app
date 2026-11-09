import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import type { DefinitionMeta, ReportResponse, Route } from "../api/types";
import type { Snapshot, SnapshotToday } from "../api/snapshots";
import { SnapshotPage } from "./SnapshotPage";

const ID = "AbCdEfGhIjKlMnOpQrStUv";

const DEFINITION: DefinitionMeta = {
  preset: "standard_m1_p5",
  early_tolerance_sec: 60,
  late_tolerance_sec: 300,
  early_departure_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
  method_version: "2026.11.0",
  source: "aggregates",
  built_at: "2026-11-05T15:30:00Z",
  value_basis: "feed_prediction",
  timetable_versions: null,
  source_mix: null,
};

const CTX = { from: "2026-10-01", to: "2026-10-31", dow: "all", time_band: "all", service: "all", routes: [] };

const ROUTES: Route[] = [
  {
    route_id: "42",
    route_short_name: "42",
    route_long_name: "Hillside line",
    route_code: "42",
    trip_headsigns: [],
    origin_name: null,
    via_name: null,
    destination_name: null,
    is_loop: false,
    bidirectional: false,
  },
];

function routeTable(text: string): ReportResponse {
  return {
    report_type: "route_table",
    rendered_at: "2026-11-09T05:05:00Z",
    text,
    rows: [["42", "10.60", "12.3", "40.1", 496, 1236, "3.5", false, 1.9]],
    ctx: CTX,
    definition: DEFINITION,
    scope_applied: {},
    sort: "on_time",
    order: "worst",
    rows_total: 174,
    reliable_min_samples: 100,
  } as ReportResponse;
}

function council(text: string): ReportResponse {
  return {
    report_type: "council_summary",
    rendered_at: "2026-11-09T05:05:00Z",
    text,
    rows: [[22.6, 2.5, 540357, 2130, 2100, 98.6, 4.2]],
    ctx: CTX,
    definition: DEFINITION,
    scope_applied: {},
    delivered_scope_applied: {},
    covered_days: 31,
    window_days: 31,
    delivered_missing: null,
  } as ReportResponse;
}

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    snapshot_id: ID,
    created_at: "2026-11-09T05:05:00Z",
    screen: "route_table",
    agency_id: 7,
    agency_name: "Hill Transit",
    params: {},
    payloads: {
      report: { ja: routeTable("路線の表"), en: routeTable("Route table") }, // i18n-ignore: fixture prose
      routes: ROUTES,
      credit: { ja: "出典：テスト", en: "Source: test" }, // i18n-ignore: fixture prose
    },
    method_version: "2026.11.0",
    title: null,
    note: null,
    ...overrides,
  };
}

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The snapshot read, and today's figures at its `/today`, each answered
 *  afresh on every call. */
function stubApi(stored: Snapshot, today: { status: number; body: SnapshotToday | { detail: string } }) {
  const fetchMock = vi.fn((url: RequestInfo | URL) => {
    const { status, body } = String(url).endsWith("/today") ? today : { status: 200, body: stored };
    return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function tableRow(code: string, avg: string, onTime: string, samples = 1236): unknown[] {
  return [code, avg, onTime, "40.1", 496, samples, "3.5", false, 1.9];
}

function withRows(report: ReportResponse, rows: unknown[][], definition: Partial<DefinitionMeta> = {}): ReportResponse {
  return { ...report, rows, definition: { ...report.definition, ...definition } } as ReportResponse;
}

function renderPage() {
  const router = createMemoryRouter([{ path: "/s/:snapshotId", element: <SnapshotPage /> }], {
    initialEntries: [`/s/${ID}`],
  });
  renderWithProviders(<RouterProvider router={router} />);
}

describe("SnapshotPage", () => {
  afterEach(async () => {
    vi.unstubAllGlobals();
    await i18n.changeLanguage("en");
  });

  it("states when it was taken, in JST, and the method the figures were made with", async () => {
    stubFetch(200, snapshot());
    renderPage();
    expect(await screen.findByText("Snapshot (created 2026-11-09 14:05 JST, method 2026.11.0)")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Delays by route" })).toBeInTheDocument();
  });

  it("reads only the snapshot itself, never a live endpoint", async () => {
    const fetchMock = stubFetch(200, snapshot());
    renderPage();
    await screen.findByRole("table");
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([expect.stringMatching(new RegExp(`/api/snapshots/${ID}$`))]);
  });

  it("draws the stored Routes table with the stored route labels and sort", async () => {
    stubFetch(200, snapshot());
    renderPage();
    const table = await screen.findByRole("table");
    expect(within(table).getByText(/Hillside line/)).toBeInTheDocument();
    expect(within(table).getByText("10.6")).toBeInTheDocument();
    expect(within(table).getByRole("columnheader", { name: /On-time %/ })).toHaveAttribute("aria-sort", "ascending");
    expect(within(table).queryByRole("link")).toBeNull();
    expect(screen.getByText("Source: test")).toBeInTheDocument();
  });

  it("lays the stored council report out as the live screen does, in the reader's language", async () => {
    const prose = { ja: "定時率は22.6%でした。", en: "On time 22.6% of the time." }; // i18n-ignore: fixture prose
    stubFetch(200, snapshot({ screen: "council_report", payloads: { report: { ja: council(prose.ja), en: council(prose.en) }, routes: [], credit: { ja: null, en: null } } }));
    renderPage();
    const figures = await screen.findByRole("list", { name: "Headline figures" });
    expect(figures).toHaveTextContent("22.6%");
    expect(figures).toHaveTextContent("540,357");
    expect(screen.getByText(prose.en)).toBeInTheDocument();
    await i18n.changeLanguage("ja");
    expect(await screen.findByText(prose.ja)).toBeInTheDocument();
  });

  it("names every condition the figures were computed under, marking one the screen did not use", async () => {
    const params = { from: "2026-10-01", to: "2026-10-31", dow: "weekday", time_band: "morning", service: "all", routes: ["42"], hour: null, stop: null, dir: null, late: 120, early: null };
    const report = { ...routeTable("Route table"), scope_applied: { from: true, to: true, dow: true, time_band: false, routes: true, late: true } } as ReportResponse;
    stubFetch(200, snapshot({ params, payloads: { report: { en: report }, routes: ROUTES, credit: {} } }));
    renderPage();
    const scope = await screen.findByTestId("snapshot-scope");
    expect(scope).toHaveTextContent("Hill Transit");
    expect(scope).toHaveTextContent("Hillside line");
    expect(scope).toHaveTextContent("weekdays");
    expect(scope).toHaveTextContent("within 2 min");
    const unused = within(scope).getByText("Morning (05–09)");
    expect(unused.closest("s")).not.toBeNull();
    expect(unused.closest("s")).toHaveAttribute("title", "This screen doesn't use this condition");
  });

  it("states the stored table's floor and left-out routes without offering to change them", async () => {
    const params = { from: "2026-10-01", to: "2026-10-31", include_sparse: true, include_retired: false, include_zero_delay: false };
    const report = { ...routeTable("Route table"), retired_rows: 3, zero_delay_rows: 0 } as ReportResponse;
    stubFetch(200, snapshot({ params, payloads: { report: { en: report }, routes: ROUTES, credit: {} } }));
    renderPage();
    const sparse = await screen.findByRole("checkbox", { name: "Include routes observed fewer than 100 times" });
    expect(sparse).toBeChecked();
    expect(sparse).toBeDisabled();
    const retired = screen.getByRole("checkbox", { name: "Include 3 route codes the current timetable no longer runs" });
    expect(retired).not.toBeChecked();
    expect(retired).toBeDisabled();
  });

  it("shows the snapshot's own title and note when it has them", async () => {
    stubFetch(200, snapshot({ title: "October council", note: "For the 11/20 meeting" }));
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "October council" })).toBeInTheDocument();
    expect(screen.getByText("For the 11/20 meeting")).toBeInTheDocument();
  });

  it("says calmly that an unknown or deleted link has no snapshot", async () => {
    stubFetch(404, { detail: "snapshot not found" });
    renderPage();
    expect(await screen.findByText("Snapshot not found")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("SnapshotPage compared with today", () => {
  afterEach(async () => {
    vi.unstubAllGlobals();
    await i18n.changeLanguage("en");
  });

  const stored = withRows(routeTable("Route table"), [tableRow("42", "10.60", "12.3"), tableRow("9", "3.00", "80.0")]);
  const storedSnapshot = snapshot({ payloads: { report: { en: stored, ja: stored }, routes: ROUTES, credit: {} } });

  it("keeps the comparison off until the reader turns it on, asking nothing of today meanwhile", async () => {
    const fetchMock = stubApi(storedSnapshot, { status: 200, body: { report: stored, unavailable: null } });
    renderPage();
    const toggle = await screen.findByRole("checkbox", { name: "Compare with today's figures" });
    expect(toggle).not.toBeChecked();
    expect(screen.queryByText(/^today /)).toBeNull();
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([expect.stringMatching(new RegExp(`/api/snapshots/${ID}$`))]);
  });

  it("marks each moved figure with today's value, and each route only one side lists, leaving equal figures as they are", async () => {
    const today = withRows(routeTable("Route table"), [tableRow("42", "10.64", "15.0"), tableRow("7", "4.00", "70.0")]);
    const fetchMock = stubApi(storedSnapshot, { status: 200, body: { report: today, unavailable: null } });
    renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Compare with today's figures" }));

    const table = await screen.findByRole("table");
    const moved = await within(table).findByText("today 15.0%");
    expect(moved.closest("td")).toHaveTextContent("12.3%");
    // 10.60 and 10.64 both print 10.6: nothing extra beside it.
    expect(within(table).getByText("10.6").closest("td")?.textContent).toBe("10.6");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[1]).toHaveTextContent("only in the snapshot");
    expect(rows[2]).toHaveTextContent("only today");
    expect(rows[2]).toHaveTextContent("70.0%");
    expect(screen.getByText("3 differences from today's figures.")).toBeInTheDocument();

    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls).toEqual([expect.stringMatching(new RegExp(`/api/snapshots/${ID}$`)), expect.stringMatching(new RegExp(`/api/snapshots/${ID}/today$`))]);
  });

  it("keeps the stored rows' bars as they were when a route only today lists runs longer", async () => {
    const byAvg = { ...withRows(routeTable("Route table"), [tableRow("42", "10.60", "12.3"), tableRow("9", "3.00", "80.0")]), sort: "avg" } as ReportResponse;
    const today = { ...withRows(byAvg, [tableRow("42", "10.60", "12.3"), tableRow("7", "20.00", "50.0")]), sort: "avg" } as ReportResponse;
    stubApi(snapshot({ payloads: { report: { en: byAvg, ja: byAvg }, routes: ROUTES, credit: {} } }), {
      status: 200,
      body: { report: today, unavailable: null },
    });
    renderPage();
    const fill = (row: HTMLElement) => (row.querySelector('[data-testid="bar-track"] > div') as HTMLElement).style.width;
    const first = () => within(screen.getByRole("table")).getAllByRole("row")[1];
    await screen.findByRole("table");
    const before = fill(first());
    await userEvent.click(screen.getByRole("checkbox", { name: "Compare with today's figures" }));
    await screen.findByText("only today");
    expect(fill(first())).toBe(before);
  });

  it("names each side's method, build and on-time definition, and says plainly when the method differs", async () => {
    const today = withRows(stored, stored.rows as unknown[][], { method_version: "2026.12.0", built_at: "2026-12-01T03:00:00Z" });
    stubApi(storedSnapshot, { status: 200, body: { report: today, unavailable: null } });
    renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Compare with today's figures" }));

    const sides = await screen.findByTestId("snapshot-compare-sides");
    expect(sides).toHaveTextContent("Method 2026.11.0, aggregates built 2026-11-06 00:30 JST.");
    expect(sides).toHaveTextContent("Method 2026.12.0, aggregates built 2026-12-01 12:00 JST.");
    expect(sides).toHaveTextContent("On time (industry standard)");
    expect(
      screen.getByText("Today's figures use method 2026.12.0; the snapshot used 2026.11.0. A difference may come from the method rather than the data."),
    ).toBeInTheDocument();
    expect(screen.getByText("Every figure matches today's.")).toBeInTheDocument();
  });

  it("says calmly that today's figures are unavailable when the agency is gone", async () => {
    stubApi(storedSnapshot, { status: 200, body: { report: null, unavailable: "agency_removed" } });
    renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Compare with today's figures" }));
    expect(await screen.findByText("Today's figures are unavailable: this agency is no longer in the app.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(/^today /)).toBeNull();
  });

  it("marks the council report's moved tiles with today's value, never comparing its prose", async () => {
    const before = council("On time 22.6% of the time.");
    const after = withRows(council("On time 25.0% of the time."), [[25.0, 2.5, 540357, 2130, 2100, 98.6, 4.2]]);
    stubApi(snapshot({ screen: "council_report", payloads: { report: { en: before, ja: before }, routes: [], credit: {} } }), {
      status: 200,
      body: { report: after, unavailable: null },
    });
    renderPage();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Compare with today's figures" }));
    const figures = await screen.findByRole("list", { name: "Headline figures" });
    const onTime = (await within(figures).findByText("today 25.0%")).closest("li");
    expect(onTime).toHaveTextContent("22.6%");
    expect(within(figures).getByText("540,357").closest("li")).not.toHaveTextContent("today");
    expect(screen.getByText("On time 22.6% of the time.")).toBeInTheDocument();
    expect(screen.getByText("1 difference from today's figures.")).toBeInTheDocument();
  });
});
