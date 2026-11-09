import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import i18n from "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import type { DefinitionMeta, ReportResponse, Route } from "../api/types";
import type { Snapshot } from "../api/snapshots";
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
