import { describe, it, expect, vi, beforeEach, afterAll } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { NetworkTab } from "./NetworkTab";
import { STILL_WORKING_AFTER_MS } from "../components/StillWorking";
import { stubReducedMotion } from "../test/reducedMotion";
import i18n from "../i18n";
import * as hooks from "../api/hooks";
import { ApiError } from "../api/client";
import type { DefinitionMeta, NetworkAgencyRow } from "../api/types";

function row(over: Partial<NetworkAgencyRow>): NetworkAgencyRow {
  return {
    agency_id: 1, agency_name: "A", avg_delay_min: 5, on_time_pct: 90,
    samples: 100, raw_samples: 1000, clamp_count: 5, clamp_pct: 0.5, is_stale: false,
    data_from: "2026-04-01", data_to: "2026-04-02",
    planned_trips: 120, executed_trips: 114, service_delivered_pct: 95,
    covered_days: 7, window_days: 7, delivered_missing: null,
    has_ridership_weights: false, weighted_on_time_pct: null,
    static_version_id: null, planned_trip_count: null, planned_vehicle_km: null,
    vehicle_km_delivered_pct: null, ...over,
  };
}

const definition: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function renderTab(agencyId = "1") {
  renderWithProviders(
    <MemoryRouter initialEntries={[`/agencies/${agencyId}/network`]}>
      <Routes>
        <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("NetworkTab", () => {
  beforeEach(async () => {
    stubReducedMotion();
    await i18n.changeLanguage("en");
  });
  // One test below switches to Japanese to check the locale-aware separator;
  // restore English so later test files in this run don't inherit "ja" as
  // their starting locale.
  afterAll(async () => await i18n.changeLanguage("en"));

  it("titles the agencies board as a section of the Compare page, which owns the level-1 heading", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", definition, agencies: [row({ agency_id: 1, agency_name: "Hiroden" })] },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: "Compare agencies" })).toBeInTheDocument();
  });

  it("renders one row per agency with stale badge, no-data dash, clamp % dot", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", avg_delay_min: 10, on_time_pct: 50, clamp_pct: 0.14 }),
          row({ agency_id: 2, agency_name: "HiroBus", avg_delay_min: 4, on_time_pct: 88, clamp_pct: 10, is_stale: true, data_from: "2026-04-03", data_to: "2026-04-04" }),
          row({ agency_id: 3, agency_name: "Aomori", avg_delay_min: null, on_time_pct: null, samples: 0, clamp_pct: null, data_from: null, data_to: null }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const names = screen.getAllByText(/Hiroden|HiroBus|Aomori/).map((n) => n.textContent);
    expect(names).toEqual(["Hiroden", "HiroBus", "Aomori"]);
    expect(screen.getAllByTestId("network-row")).toHaveLength(3);
    expect(screen.getByText(/\+10\.0/)).toBeInTheDocument();
    expect(screen.getByText("50.0%")).toBeInTheDocument(); // Hiroden's on-time %
    expect(screen.getByText("10.0%")).toBeInTheDocument(); // HiroBus's clamp % (secondary line, shown since 10% > 1% threshold)
    const staleBadge = screen.getByText("Behind");
    expect(staleBadge).toBeInTheDocument();
    // The badge explains itself through the shared, keyboard-reachable Tooltip, never a native title=.
    expect(staleBadge).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    fireEvent.focusIn(staleBadge);
    expect(screen.getByRole("tooltip")).toHaveTextContent(/haven't caught up yet/);
    fireEvent.focusOut(staleBadge);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(1);
    // clamp dot boundary: present only for HiroBus (10%), absent for
    // Hiroden (0.14 < 1) and Aomori (null) — no secondary line at all for
    // Hiroden since neither clamp nor stale triggers.
    expect(screen.getAllByTestId("clamp-dot")).toHaveLength(1);
    expect(screen.getByText("Apr 1 – Apr 2, 2026")).toBeInTheDocument();
    expect(screen.getByText("no data in range")).toBeInTheDocument();
    expect(screen.getByText("How to read this")).toBeInTheDocument();
  });

  it("shows the service-delivered % next to on-time %, and a dash when not available", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", service_delivered_pct: 96.7, planned_trips: 300, executed_trips: 290 }),
          // No populated schedule_relationship data for this agency's feed —
          // must read as "not available" (a dash), never a misleading 100%.
          row({ agency_id: 2, agency_name: "Aomori", service_delivered_pct: null, planned_trips: 0, executed_trips: null }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    // The secondary figures carry their own label in the row's meta
    // line, so the value is not an element's whole text.
    expect(screen.getByText(/96\.7%/)).toBeInTheDocument();
    const aomoriCard = screen.getByText("Aomori").closest(".network-row");
    expect(aomoriCard).toHaveTextContent("—");
  });

  it("says on the planned figure how many of the period's days a timetable plans, rate or no rate, with a footnote", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-03-02", to: "2026-03-31", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", planned_trips: 57887, service_delivered_pct: 96.7, covered_days: 16, window_days: 30 }),
          row({ agency_id: 2, agency_name: "HiroBus", planned_trips: 18415, service_delivered_pct: 98.1, covered_days: 30, window_days: 30 }),
          // The plan is partial whether or not the feed can measure a rate.
          row({ agency_id: 3, agency_name: "Geiyo", planned_trips: 7353, service_delivered_pct: null, executed_trips: null, covered_days: 13, window_days: 30, delivered_missing: "feed_unverified" }),
          // No plan at all: there is nothing to qualify.
          row({ agency_id: 4, agency_name: "Onomichi", planned_trips: 0, service_delivered_pct: null, executed_trips: null, covered_days: 0, window_days: 30, delivered_missing: "no_plan" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const card = (name: string) => screen.getByText(name).closest(".network-row");
    expect(card("Hiroden")).toHaveTextContent("Planned trips 57,887 (16/30 days a timetable plans)");
    expect(card("Hiroden")).toHaveTextContent("Service delivered % 96.7%");
    expect(card("HiroBus")).not.toHaveTextContent("a timetable plans");
    expect(card("Geiyo")).toHaveTextContent("Planned trips 7,353 (13/30 days a timetable plans)");
    expect(card("Onomichi")).not.toHaveTextContent("a timetable plans");
    expect(screen.getByTestId("network-plan-coverage-footnote")).toHaveTextContent(
      /count only the days a timetable plans/,
    );
  });

  it("writes the timetable coverage note in Japanese", async () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-03-02", to: "2026-03-31", definition,
        agencies: [row({ agency_id: 1, agency_name: "Hiroden", service_delivered_pct: 96.7, covered_days: 16, window_days: 30 })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    await i18n.changeLanguage("ja");
    renderTab();
    expect(screen.getByText("Hiroden").closest(".network-row")).toHaveTextContent("計画便数 120便（時刻表に計画がある16/30日分）");
    expect(screen.getByTestId("network-plan-coverage-footnote")).toHaveTextContent(
      "時刻表に計画がある日だけを数えています",
    );
  });

  it("leaves the coverage footnote out when every plan spans the whole period or there is none", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-03-02", to: "2026-03-31", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", service_delivered_pct: 96.7, covered_days: 30, window_days: 30 }),
          row({ agency_id: 2, agency_name: "Onomichi", planned_trips: 0, service_delivered_pct: null, executed_trips: null, covered_days: 0, window_days: 30, delivered_missing: "no_plan" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByText(/a timetable plans/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("network-plan-coverage-footnote")).not.toBeInTheDocument();
  });

  it("prints the period's planned trips, never the loaded timetable's trip definitions", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-09-24", to: "2026-10-23", definition,
        agencies: [
          row({
            agency_id: 8, agency_name: "Hiroden", planned_trips: 57887, covered_days: 30, window_days: 30,
            static_version_id: "gtfs_static_20261001", planned_trip_count: 9632, planned_vehicle_km: 115756.3,
            service_delivered_pct: null, executed_trips: null, vehicle_km_delivered_pct: null, delivered_missing: "feed_unverified",
          }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const card = screen.getByText("Hiroden").closest(".network-row");
    expect(card).toHaveTextContent("Planned trips 57,887");
    expect(card).not.toHaveTextContent("9,632");
  });

  it("says why a delivered rate is missing, and never prints 0 planned trips for a read that failed", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Unverified", service_delivered_pct: null, executed_trips: null, delivered_missing: "feed_unverified" }),
          row({ agency_id: 2, agency_name: "Aomori", service_delivered_pct: null, executed_trips: null, delivered_missing: "feed_cannot_report" }),
          row({ agency_id: 3, agency_name: "Planless", planned_trips: 0, covered_days: 0, service_delivered_pct: null, executed_trips: null, delivered_missing: "no_plan" }),
          row({ agency_id: 4, agency_name: "Unread", planned_trips: 0, covered_days: 0, service_delivered_pct: null, executed_trips: null, delivered_missing: "unreadable" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const card = (name: string) => screen.getByText(name).closest(".network-row");
    expect(card("Unverified")).toHaveTextContent("Service delivered % — (the feed is not yet checked for cancellations)");
    expect(card("Aomori")).toHaveTextContent("Service delivered % — (this feed format cannot report cancellations)");
    expect(card("Planless")).toHaveTextContent("Service delivered % — (no timetable plans this period)");
    expect(card("Planless")).toHaveTextContent("Planned trips 0");
    expect(card("Unread")).toHaveTextContent("Service delivered % — (the plan could not be read)");
    expect(card("Unread")).toHaveTextContent("Planned trips —");
    expect(card("Unread")).not.toHaveTextContent("Planned trips 0");
  });

  it("shows vehicle-km delivered % where it is computed, says why where only it is missing, and never calls vehicle-km itself unavailable", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", planned_vehicle_km: 1234.5, vehicle_km_delivered_pct: 91.2, service_delivered_pct: 91.2 }),
          // A rate, but no route shapes to turn it into vehicle-km.
          row({ agency_id: 2, agency_name: "HiroBus", planned_vehicle_km: null, vehicle_km_delivered_pct: null, service_delivered_pct: 95 }),
          // No rate: the same reason covers both, so vehicle-km says nothing more.
          row({ agency_id: 3, agency_name: "Aomori", planned_vehicle_km: 52991.9, vehicle_km_delivered_pct: null, service_delivered_pct: null, executed_trips: null, delivered_missing: "feed_cannot_report" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const card = (name: string) => screen.getByText(name).closest(".network-row");
    expect(card("Hiroden")).toHaveTextContent("Vehicle-km delivered % 91.2%");
    expect(card("HiroBus")).toHaveTextContent("Vehicle-km delivered % — (no route shapes loaded)");
    expect(card("Aomori")).not.toHaveTextContent("Vehicle-km delivered %");
    expect(document.body).not.toHaveTextContent("vehicle-km not available");
  });

  it("marks an agency whose data stopped, as the agency picker does, and lists it after the current ones", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-09-28", to: "2026-10-27", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Aomori", avg_delay_min: 5, data_from: "2026-09-28", data_to: "2026-10-14" }),
          row({ agency_id: 8, agency_name: "Hiroden", avg_delay_min: 3, data_from: "2026-09-28", data_to: "2026-10-23" }),
          // Two days behind the newest closes by itself; not stopped.
          row({ agency_id: 9, agency_name: "HiroBus", avg_delay_min: 2, data_from: "2026-09-28", data_to: "2026-10-21" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const names = screen.getAllByTestId("network-row").map((r) => r.querySelector("a")?.textContent);
    expect(names).toEqual(["Hiroden", "HiroBus", "Aomori"]);
    const card = (name: string) => screen.getByText(name).closest(".network-row");
    expect(card("Aomori")).toHaveTextContent("No updates after Oct 14");
    expect(card("HiroBus")).not.toHaveTextContent("No updates after");
  });

  it("writes the period and each agency's own data range in one date style, the range only where it differs", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Whole", data_from: "2026-04-01", data_to: "2026-04-07" }),
          row({ agency_id: 2, agency_name: "Part", data_from: "2026-04-01", data_to: "2026-04-06" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/network?from=2026-04-01&to=2026-04-07"]}>
        <Routes>
          <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByText("Across all agencies · Apr 1 – Apr 7, 2026")).toBeInTheDocument();
    expect(screen.getByText("Whole").closest(".network-row")).not.toHaveTextContent("Apr");
    expect(screen.getByText("Part").closest(".network-row")).toHaveTextContent("Apr 1 – Apr 6, 2026");
  });

  it("leaves the route and stop filter off the agency links, since their codes belong to one agency", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 8, agency_name: "Aomori" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/7/network?from=2026-04-01&to=2026-04-07&dow=weekday&routes=50&stop=S1"]}>
        <Routes>
          <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
        </Routes>
      </MemoryRouter>,
    );
    const href = screen.getByRole("link", { name: "Aomori" }).getAttribute("href") ?? "";
    const query = new URLSearchParams(href.split("?")[1]);
    expect(href.startsWith("/agencies/8/live?")).toBe(true);
    expect(query.has("routes")).toBe(false);
    expect(query.has("stop")).toBe(false);
    expect(query.get("from")).toBe("2026-04-01");
    expect(query.get("dow")).toBe("weekday");
  });

  it("links each agency name to its operations view, carrying the current range", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 7, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/7/network?from=2026-04-01&to=2026-04-07"]}>
        <Routes>
          <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Hiroden" });
    expect(link).toHaveAttribute(
      "href",
      "/agencies/7/live?from=2026-04-01&to=2026-04-07",
    );
    // The description moved off `title` and onto the Tooltip primitive, which
    // shows on focus as well as hover and is wired as aria-describedby.
    expect(link).not.toHaveAttribute("title");
    act(() => {
      fireEvent.focus(link);
    });
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Open Hiroden in Live");
    expect(link.getAttribute("aria-describedby")).toBe(tip.id);
  });

  it("renders the empty message and no agency cards when there are no agencies", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", agencies: [], definition },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByText("No agencies.")).toBeInTheDocument();
    expect(screen.queryByTestId("network-card-list")).not.toBeInTheDocument();
  });

  it("shows a skeleton (no cards) while pending", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: true, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByTestId("network-card-list")).not.toBeInTheDocument();
  });

  it("says a slow comparison is still working, and offers the period's last week", () => {
    vi.useFakeTimers();
    try {
      vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
        data: undefined, isPending: true, error: null, refetch: vi.fn(),
      } as never);
      renderTab();
      act(() => vi.advanceTimersByTime(STILL_WORKING_AFTER_MS));
      expect(screen.getByText("Still working: a long period takes longer to count.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Narrow to the last 7 days of the period" })).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the error banner with a retry on error", () => {
    // A non-transient class (server 5xx) so the banner (and its retry
    // button) shows immediately -- a plain network/timeout error auto-
    // retries quietly first (AsyncSection's useAutoRetry).
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: undefined, isPending: false, error: new ApiError(500, "boom"), refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("marks the agency selected in the URL as the one being viewed, not as the visitor's own", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden" }),
          row({ agency_id: 2, agency_name: "HiroBus" }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab("2"); // viewing agency 2 (HiroBus)
    const badges = screen.getAllByTestId("viewing-badge");
    expect(badges).toHaveLength(1);
    expect(badges[0]).toHaveTextContent("Viewing");
    expect(screen.queryByText("YOU")).not.toBeInTheDocument();
    // the badge sits inside HiroBus's row, not Hiroden's
    const hiroBusCard = screen.getByText("HiroBus").closest(".network-row");
    expect(hiroBusCard).toContainElement(badges[0]);
  });

  it("shows no viewing badge when there is no agencyId in the URL", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 1, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/network"]}>
        <NetworkTab />
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("viewing-badge")).not.toBeInTheDocument();
  });

  it("writes the coverage range in the language's date style", async () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 1, agency_name: "Hiroden", data_from: "2026-04-01", data_to: "2026-04-02" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    await i18n.changeLanguage("ja");
    renderTab();
    expect(screen.getByText("2026年4月1日〜4月2日")).toBeInTheDocument();
  });

  it("keeps the definition metadata behind the aggregation-conditions disclosure", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 1, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    // Behind one "How this is calculated": a click away, not competing
    // with the comparison it annotates, and not nested in a second one.
    const block = screen.getByTestId("definition-meta");
    expect(block.tagName).toBe("DETAILS");
    expect(block).not.toHaveAttribute("open");
    expect(block.parentElement?.closest("details")).toBeNull();
    expect(block).toHaveTextContent("On time = no more than 1 min late (early departures count as on time).");
  });

  it("hides the ridership-weighted toggle when no agency has configured weights", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [row({ agency_id: 1, agency_name: "Hiroden" })],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.queryByTestId("ridership-weighted-toggle")).not.toBeInTheDocument();
  });

  it("shows the ridership-weighted toggle and swaps the on-time figure when checked", async () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", on_time_pct: 50, has_ridership_weights: true, weighted_on_time_pct: 82.7 }),
          // No configured weight for this agency -- toggling on must leave
          // its own figure alone (there's nothing weighted to show).
          row({ agency_id: 2, agency_name: "HiroBus", on_time_pct: 88, has_ridership_weights: false, weighted_on_time_pct: null }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const toggle = screen.getByTestId("ridership-weighted-toggle");
    expect(screen.getByText("50.0%")).toBeInTheDocument();
    expect(screen.getByText("88.0%")).toBeInTheDocument();

    await userEvent.click(toggle);

    expect(screen.getByText("82.7% (weighted)")).toBeInTheDocument();
    expect(screen.getByText("88.0%")).toBeInTheDocument(); // HiroBus unchanged: not configured
  });


  it("prints each agency's delay figure in the text colour", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", definition, agencies: [row({ agency_id: 1, agency_name: "Worst", avg_delay_min: 6.4 })] },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const figure = screen.getByTestId("network-row").querySelector(".network-row__unit")!.parentElement as HTMLElement;
    expect(figure.style.color).toBe("");
  });

  it("sorts rows worst-delay-first regardless of the order the API returned, nulls last", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Mild", avg_delay_min: 1.2 }),
          row({ agency_id: 2, agency_name: "Unknown", avg_delay_min: null }),
          row({ agency_id: 3, agency_name: "Worst", avg_delay_min: 4.8 }),
          row({ agency_id: 4, agency_name: "Middle", avg_delay_min: 3.1 }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const names = screen.getAllByTestId("network-row").map((r) => r.querySelector("a")?.textContent);
    expect(names).toEqual(["Worst", "Middle", "Mild", "Unknown"]);
  });

  it("draws every bar against one shared axis, stepped up from 6 minutes to hold the largest agency", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          // 7.5 min is past 6, so the shared axis steps up to 12 for every row.
          row({ agency_id: 1, agency_name: "Worst", avg_delay_min: 7.5 }),
          row({ agency_id: 2, agency_name: "Quarter", avg_delay_min: 3 }),
          row({ agency_id: 3, agency_name: "None", avg_delay_min: null }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const widths = screen.getAllByTestId("network-axis-fill").map((el) => el.style.width);
    expect(widths).toEqual(["62.5%", "25%", "0%"]);
    expect(screen.getByText("Shared axis · 0–12 min")).toBeInTheDocument();
    // Filled with the shared delay steps, not the old severe ramp.
    expect(screen.getAllByTestId("network-axis-fill")[0].style.background).toBe("var(--d4)");
  });

  it("shows the observation count as text, with no bar encoding it", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 1, agency_name: "Hiroden", avg_delay_min: 3, samples: 233148 }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getByText(/233,148/)).toBeInTheDocument();
    // One bar per row, and its length is the delay (3 of 6 minutes), never
    // the sample count.
    const fills = screen.getAllByTestId("network-axis-fill");
    expect(fills).toHaveLength(1);
    expect(fills[0].style.width).toBe("50%");
  });

  it("gives each row a stable flip key so a re-sort moves nodes instead of remounting them", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: [
          row({ agency_id: 7, agency_name: "Hiroden", avg_delay_min: 4 }),
          row({ agency_id: 9, agency_name: "HiroBus", avg_delay_min: 2 }),
        ],
      },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    expect(screen.getAllByTestId("network-row").map((r) => r.dataset.flipKey)).toEqual(["7", "9"]);
  });

  it("sets both range date inputs' lang attribute to the active UI language", () => {
    vi.spyOn(hooks, "useNetworkSummary").mockReturnValue({
      data: { from: "2026-04-01", to: "2026-04-07", agencies: [], definition },
      isPending: false, error: null, refetch: vi.fn(),
    } as never);
    renderTab();
    const inputs = document.querySelectorAll("input[type='date']");
    expect(inputs.length).toBe(2);
    inputs.forEach((el) => expect(el.getAttribute("lang")).toBe("en"));
  });

  it("keeps a raised agency cap across a refetch of the same range and re-caps when the range changes", async () => {
    const user = userEvent.setup();
    // A fresh payload per call: every refetch hands back new objects even
    // when nothing in them changed.
    const summary = () => ({
      data: {
        from: "2026-04-01", to: "2026-04-07", definition,
        agencies: Array.from({ length: 250 }, (_, i) => row({ agency_id: i + 1, agency_name: `Agency ${i + 1}` })),
      },
      isPending: false, error: null, refetch: vi.fn(),
    });
    let current = summary();
    vi.spyOn(hooks, "useNetworkSummary").mockImplementation(() => current as never);
    const ui = () => (
      <MemoryRouter initialEntries={["/agencies/1/network?from=2026-04-01&to=2026-04-07"]}>
        <Routes>
          <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
        </Routes>
      </MemoryRouter>
    );
    const { rerender } = renderWithProviders(ui());
    expect(screen.getAllByTestId("network-row")).toHaveLength(200);
    await user.click(screen.getByRole("button", { name: "Show 50 more" }));
    expect(screen.getAllByTestId("network-row")).toHaveLength(250);

    current = summary();
    rerender(ui());
    expect(screen.getAllByTestId("network-row")).toHaveLength(250);

    fireEvent.change(document.querySelector("input[type='date']")!, { target: { value: "2026-03-25" } });
    expect(screen.getAllByTestId("network-row")).toHaveLength(200);
    // The cap is 200 rows, so proving it renders a few hundred rows several
    // times; on a loaded machine that alone outlasts the default timeout.
  }, 45_000);

  it.each([
    [-1.2, 0.8, "+0.8"],
    [0.8, -1.2, "-1.2"],
  ])("signs every frame of the delay figure's tween from %s to %s by the value it shows", (from, to, settled) => {
    vi.spyOn(window, "matchMedia").mockImplementation((query: string) => ({
      matches: false, media: query, onchange: null,
      addListener: () => {}, removeListener: () => {},
      addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList);
    const frames: FrameRequestCallback[] = [];
    const raf = vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => frames.push(cb));
    const caf = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
    try {
      const summary = (avg: number) => ({
        data: { from: "2026-04-01", to: "2026-04-07", definition, agencies: [row({ avg_delay_min: avg })] },
        isPending: false, error: null, refetch: vi.fn(),
      });
      let current = summary(from);
      vi.spyOn(hooks, "useNetworkSummary").mockImplementation(() => current as never);
      const ui = () => (
        <MemoryRouter initialEntries={["/agencies/1/network"]}>
          <Routes>
            <Route path="/agencies/:agencyId/network" element={<NetworkTab />} />
          </Routes>
        </MemoryRouter>
      );
      const { rerender } = renderWithProviders(ui());
      current = summary(to);
      rerender(ui());

      const figure = () => document.querySelector(".network-row__value")!.textContent!.replace(/min$/, "");
      const shown: string[] = [];
      for (let now = 0; now <= 600; now += 25) {
        act(() => frames.splice(0).forEach((cb) => cb(now)));
        shown.push(figure());
      }
      for (const s of shown) expect(s).toMatch(/^[+-]\d+\.\d$/);
      // The tween crossed zero, so both signs were on screen at some frame.
      expect(shown.some((s) => s.startsWith("-"))).toBe(true);
      expect(shown.some((s) => s.startsWith("+") && s !== "+0.0")).toBe(true);
      expect(shown.at(-1)).toBe(settled);
    } finally {
      raf.mockRestore();
      caf.mockRestore();
    }
  });
});
