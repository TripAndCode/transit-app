import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import type { DefinitionMeta, ReportResponse } from "../api/types";
import { RouteTableScreen } from "./RouteTableScreen";

vi.mock("../components/HeadwayQualityPanel", () => ({ HeadwayQualityPanel: () => <div>headway-panel</div> }));
vi.mock("../components/PerformanceStandardPanel", () => ({ PerformanceStandardPanel: () => <div>standards-panel</div> }));

const DEFINITION: DefinitionMeta = {
  preset: "legacy_60s",
  early_tolerance_sec: null,
  late_tolerance_sec: 60,
  exclusion_threshold_sec: 7200,
  measurement_point: "all_stops_all_observations",
  dedup_rule: "latest_observation_per_stop_event",
};

function tableResponse(overrides: Record<string, unknown> = {}): ReportResponse {
  return {
    report_type: "route_table",
    rendered_at: "2026-10-24T00:00:00Z",
    text: "",
    rows: [
      ["42", "10.60", "12.3", "40.1", 496, 1236, false, 1.9],
      ["70-3", "7.97", "11.3", "64.2", 28641, 44609, false, 0.3],
    ],
    ctx: { from: "2026-09-24", to: "2026-10-23", dow: "all", time_band: "all", service: "all", routes: [] },
    definition: DEFINITION,
    scope_applied: {},
    sort: "avg",
    order: "worst",
    rows_total: 174,
    reliable_min_samples: 100,
    retired_rows: 12,
    zero_delay_rows: 1,
    ...overrides,
  } as ReportResponse;
}

function setup(path: string, response: ReportResponse | undefined = tableResponse(), extra: Record<string, unknown> = {}) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useSuggestion").mockReturnValue({ data: null, isLoading: false } as never);
  const useReport = vi
    .spyOn(hooks, "useReport")
    .mockReturnValue({ data: response, isFetching: false, error: null, refetch: vi.fn(), ...extra } as never);
  const router = createMemoryRouter([{ path: "/agencies/:agencyId/routes", element: <RouteTableScreen /> }], {
    initialEntries: [path],
  });
  renderWithProviders(<RouterProvider router={router} />);
  const params = () => new URLSearchParams(router.state.location.search);
  const options = () => useReport.mock.calls.at(-1)?.[3];
  return { useReport, router, params, options };
}

const pressed = (name: string) => screen.getByRole("button", { name }).getAttribute("aria-pressed");
const csvHref = () => screen.getByRole("link", { name: /CSV/ }).getAttribute("href") ?? "";

describe("RouteTableScreen", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens on every route sorted by average delay, worst first, fifty at a time", () => {
    const { useReport, options } = setup("/agencies/1/routes");
    expect(useReport.mock.calls.at(-1)?.[1]).toBe("route_table");
    expect(options()).toEqual({
      sort: "avg",
      order: undefined,
      includeSparse: false,
      includeRetired: false,
      includeZeroDelay: false,
      limit: 50,
    });
    expect(pressed("Average delay")).toBe("true");
    expect(pressed("Worst first")).toBe("true");
    expect(screen.getByRole("columnheader", { name: "Avg (min)" })).toHaveAttribute("aria-sort", "descending");
  });

  it.each([
    ["?report=on_time", "on_time", "worst", "On-time rate", "Worst first", "ascending"],
    ["?report=ranking_best", "avg", "best", "Average delay", "Best first", "ascending"],
    ["?report=worst_5min", "late5_count", "worst", "Impact (count)", "Worst first", "descending"],
    ["?sort=on_time&order=best", "on_time", "best", "On-time rate", "Best first", "descending"],
    // An order left over from the on-time list never turns another list around.
    ["?report=ranking&order=best", "avg", "worst", "Average delay", "Worst first", "descending"],
  ])("opens a link from before the table, %s, sorted the way its list was", (search, sort, order, sortLabel, orderLabel, direction) => {
    const { options } = setup(`/agencies/1/routes${search}`, tableResponse({ sort, order }));
    expect(options()).toMatchObject({ sort, order: order === "best" ? "best" : undefined });
    expect(pressed(sortLabel)).toBe("true");
    expect(pressed(orderLabel)).toBe("true");
    const header = { "On-time rate": "On-time %", "Average delay": "Avg (min)", "Impact (count)": "Over 5 min (count)" }[sortLabel];
    expect(screen.getByRole("columnheader", { name: header })).toHaveAttribute("aria-sort", direction);
  });

  it("re-sorts in the URL, keeping the order and replacing a former list's report", async () => {
    const { params, options } = setup("/agencies/1/routes?report=ranking_best&from=2026-09-24");
    await userEvent.click(screen.getByRole("button", { name: "Share over 5 min" }));
    expect(params().get("sort")).toBe("late5_share");
    expect(params().get("order")).toBe("best");
    expect(params().get("report")).toBeNull();
    expect(params().get("from")).toBe("2026-09-24");
    expect(options()).toMatchObject({ sort: "late5_share", order: "best" });
    expect(csvHref()).toMatch(/[?&]sort=late5_share(&|$)/);
    expect(csvHref()).toMatch(/[?&]order=best(&|$)/);
    expect(csvHref()).toMatch(/[?&]lang=en(&|$)/);
  });

  it("turns the order around and back", async () => {
    const { params, options } = setup("/agencies/1/routes?report=on_time");
    await userEvent.click(screen.getByRole("button", { name: "Best first" }));
    expect(params().get("sort")).toBe("on_time");
    expect(params().get("order")).toBe("best");
    expect(options()).toMatchObject({ sort: "on_time", order: "best" });
    await userEvent.click(screen.getByRole("button", { name: "Worst first" }));
    expect(params().get("order")).toBeNull();
    expect(options()?.order).toBeUndefined();
  });

  it("states the window the figures cover", () => {
    setup("/agencies/1/routes");
    expect(screen.getByText(/^30 days \(/)).toBeInTheDocument();
  });

  it("says what it leaves out by default and how many, with a toggle to show each", async () => {
    const { params, options } = setup("/agencies/1/routes");
    const line = screen.getByText("Left out by default:").parentElement as HTMLElement;
    const retired = within(line).getByRole("checkbox", { name: "Include 12 route codes the current timetable no longer runs" });
    expect(within(line).getByRole("checkbox", { name: "Include 1 route whose feed always reports zero delay" })).not.toBeChecked();
    await userEvent.click(retired);
    expect(params().get("retired")).toBe("1");
    expect(options()).toMatchObject({ includeRetired: true, includeZeroDelay: false });
    expect(csvHref()).toMatch(/[?&]include_retired=1(&|$)/);
  });

  it("says nothing is left out when nothing is", () => {
    setup("/agencies/1/routes", tableResponse({ retired_rows: 0, zero_delay_rows: 0 }));
    expect(screen.queryByText("Left out by default:")).not.toBeInTheDocument();
  });

  it("ranks only well-observed routes until asked to include the rest", async () => {
    const { params, options } = setup("/agencies/1/routes");
    await userEvent.click(screen.getByRole("checkbox", { name: "Include routes observed fewer than 100 times" }));
    expect(params().get("sparse")).toBe("1");
    expect(options()).toMatchObject({ includeSparse: true });
  });

  it("says how much of the table is shown, and shows the rest on request", async () => {
    const { options } = setup("/agencies/1/routes");
    expect(screen.getByText("Showing 2 of 174")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show all" }));
    expect(options()).toMatchObject({ limit: 500 });
    expect(screen.queryByRole("button", { name: "Show all" })).not.toBeInTheDocument();
  });

  it("states what counts as on time and as over five minutes under the table", () => {
    setup("/agencies/1/routes");
    expect(screen.getByText("On time = no more than 1 min late (early departures count as on time).")).toBeInTheDocument();
    expect(screen.getByText(/^Over 5 min = departures more than 5 minutes late/)).toBeInTheDocument();
  });

  it("puts the headway and targets panels beside the on-time sort only", () => {
    setup("/agencies/1/routes?sort=on_time");
    expect(screen.getByText("headway-panel")).toBeInTheDocument();
    expect(screen.getByText("standards-panel")).toBeInTheDocument();
  });

  it("leaves the punctuality panels off the other sorts", () => {
    setup("/agencies/1/routes?sort=late5_count");
    expect(screen.queryByText("headway-panel")).toBeNull();
    expect(screen.queryByText("standards-panel")).toBeNull();
  });

  it("keeps the floor toggle in reach when no route clears it", () => {
    setup("/agencies/1/routes", tableResponse({ rows: [], rows_total: 0 }));
    expect(screen.getByText("No matching data")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Include routes observed fewer than 100 times" })).toBeInTheDocument();
  });

  it("marks the rows it holds by the sort they arrived in while another sort loads", () => {
    setup("/agencies/1/routes?sort=late5_share", tableResponse(), { isFetching: true, isPlaceholderData: true });
    expect(pressed("Share over 5 min")).toBe("true");
    expect(screen.getByRole("columnheader", { name: "Avg (min)" })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Share over 5 min" })).not.toHaveAttribute("aria-sort");
  });

  it("holds the rows it is replacing while a new sort loads, quieted", () => {
    setup("/agencies/1/routes", tableResponse(), { isFetching: true });
    expect(screen.getAllByRole("row").length).toBeGreaterThan(1);
    expect(screen.getByRole("table").closest(".route-table__body")).toHaveAttribute("aria-busy", "true");
  });
});
