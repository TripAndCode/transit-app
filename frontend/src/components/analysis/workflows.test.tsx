import { useEffect } from "react";
import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReportsHomeTab } from "../../tabs/ReportsHomeTab";
import { RouteAnalysisTab } from "../../tabs/RouteAnalysisTab";
import { readAnalyses, saveAnalysis } from "./savedAnalyses";
import { downloadCsv } from "./csv";
import { useReport, useRouteShape } from "../../api/hooks";

const mapMounts = vi.fn();
const mapProps = vi.fn();
vi.mock("./AnalysisMap", () => ({
  AnalysisMap: (props: { height: number; visible: boolean }) => {
    mapProps(props);
    useEffect(() => { mapMounts(); }, []);
    return <div>Map</div>;
  },
}));
vi.mock("./csv", () => ({ downloadCsv: vi.fn() }));
// TabFilterBar renders PresetMenu, which calls useSession -> a real
// apiGet("/api/me"). Unstubbed that fetch stays pending past the end of this
// file and destabilises whichever file vitest runs next, so it is settled
// here. Partial mock: everything else in api/auth stays real.
vi.mock("../../api/auth", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/auth")>()),
  useSession: () => ({ data: null, isPending: false }),
}));
vi.mock("../../api/hooks", () => ({
  useRoutes: () => ({ data: [
    { route_id: "a", route_code: "101", route_long_name: "Coast", route_short_name: "1", trip_headsigns: [] },
    { route_id: "b", route_code: "999", route_long_name: "Coast", route_short_name: "9", trip_headsigns: [] },
  ], isPending: false }),
  useAgencies: () => ({ data: [{ agency_id: 1, agency_name: "Test Agency" }] }),
  useRouteShape: vi.fn(() => ({ data: { route: "101", geometry: null, stops: [{ stop_id: "A", stop_sequence: 1, stop_name: "Station A", lon: 140, lat: 40, avg_min: 2, samples: 5 }] }, isPending: false })),
  useReport: vi.fn((_id, type) => ({ data: type ? { report_type: type, definition: {}, rows: type === "trend" ? [{ days: [{ date: "2026-09-07", avg_min: 2, samples: 4 }] }] : [["101", null, 2, 1, 3, 4]] } : undefined, isPending: false })),
}));
const ctx = { from: "2026-09-07", to: "2026-09-12", dow: "weekday" as const, time_band: "morning" as const, service: "all" as const, routes: ["101"] };
// Reports now renders TabFilterBar, whose PresetMenu calls useQueryClient to
// invalidate saved presets -- so the tree needs a provider even though no test
// here asserts on a query.
function show(tab: "route-analysis" | "reports", search = "") {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={[`/agencies/1/${tab}?from=${ctx.from}&to=${ctx.to}&routes=101&dow=weekday&time_band=morning${search}`]}>
        <Routes><Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} /><Route path="/agencies/:agencyId/reports" element={<ReportsHomeTab />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => { localStorage.clear(); vi.clearAllMocks(); });
it("keeps pattern and period in exported observations and saved analysis", async () => {
  show("route-analysis");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Download CSV" }));
  expect(vi.mocked(downloadCsv).mock.calls[0][1][1]).toEqual(expect.arrayContaining([1, "101", "2026-09-07", "2026-09-12", "weekday", "morning", "Station A", 2, 5]));
  await user.click(screen.getByRole("button", { name: "Save analysis" }));
  expect(readAnalyses()[0].query).toContain("routes=101");
  expect(readAnalyses()[0].query).toContain("time_band=morning");
});
it("changing keito scopes both report queries and CSV to the selected code", async () => {
  // Same guarantee as before, driven through the deferred-commit filter bar
  // this page now shares with Overview: pick inside the popover, then apply.
  // The pick alone must not reach the queries -- that is the point of the
  // pattern -- so it is asserted before the apply as well as after.
  show("reports");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Filters/ }));
  // The picker labels routes by display name, not code: "1 Coast" is 101 and
  // "9 Coast" is 999 (short name + long name, per routeDisplayName).
  await user.click(screen.getByRole("button", { name: "1 Coast" }));   // drop the initial 101
  await user.click(screen.getByRole("button", { name: "9 Coast" }));
  expect(vi.mocked(useReport).mock.calls.at(-1)?.[2].routes).toEqual(["101"]);
  await user.click(screen.getByRole("button", { name: /Apply/ }));
  expect(vi.mocked(useReport).mock.calls.at(-1)?.[2].routes).toEqual(["999"]);
  await user.click(screen.getAllByRole("button", { name: "Download CSV" })[0]);
  expect(vi.mocked(downloadCsv).mock.calls[0][1][1]).toContain("999");
});
it("saved analyses stay agency-scoped and open with their original filters", async () => {
  saveAnalysis(1, "Coast mornings", ctx, true);
  saveAnalysis(8, "Other agency", ctx, false);
  show("reports", "&view=saved");
  expect(screen.queryByText("Other agency")).toBeNull();
  expect(screen.getByRole("link", { name: "Coast mornings" }).getAttribute("href")).toContain("compare=1");
  await userEvent.setup().click(screen.getByRole("button", { name: "Delete: Coast mornings" }));
  expect(readAnalyses()).toHaveLength(1);
  expect(readAnalyses()[0].agencyId).toBe(8);
});

it("footer CSV exports the report data, not just the filter-metadata prefix", async () => {
  show("reports");
  const user = userEvent.setup();
  // Three "Download CSV" buttons exist: the trend section, the ranking
  // section, and the closing footer. Only the footer button combines both
  // datasets; the trend and ranking buttons above each export just their own
  // dataset ("changing keito scopes both report queries and CSV to the
  // selected code" covers those).
  const footerCsv = screen.getAllByRole("button", { name: "Download CSV" }).at(-1)!;
  await user.click(footerCsv);
  const payload = vi.mocked(downloadCsv).mock.calls.at(-1)![1];
  expect(payload.flat()).toContain("mean_departure_delay_minutes");
  expect(payload.flat()).toContain("median_minutes");
  expect(payload.length).toBe(8);
});

it("defers the analysis filters until Apply instead of querying mid-selection", async () => {
  // This screen renders only while exactly one pattern is selected, and
  // narrowing to one passes through a multi-code state, so committing each
  // step dropped it into its empty state mid-selection. The selection must
  // therefore not reach the shape query until Apply.
  show("route-analysis");
  const user = userEvent.setup();
  // The tab calls useRouteShape twice per render -- once for the period and
  // once for the comparison window, which passes null while compare is off --
  // so the last non-null argument is the route actually being requested.
  const lastRequestedRoute = () =>
    vi.mocked(useRouteShape).mock.calls.filter((c) => c[1] != null).at(-1)?.[1];
  expect(lastRequestedRoute()).toBe("101");

  await user.selectOptions(screen.getByRole("combobox", { name: "Service pattern" }), "999");
  expect(lastRequestedRoute()).toBe("101");
  expect(screen.queryByText("Choose a route and service pattern")).toBeNull();

  await user.click(screen.getByRole("button", { name: "Apply" }));
  expect(lastRequestedRoute()).toBe("999");
});

it("keeps the route map mounted across tab switches instead of recreating its WebGL context", async () => {
  show("route-analysis");
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Map" }));
  expect(mapMounts).toHaveBeenCalledTimes(1);
  expect(mapProps).toHaveBeenLastCalledWith(expect.objectContaining({ visible: true }));

  await user.click(screen.getByRole("tab", { name: "Delay trend" }));
  expect(mapProps).toHaveBeenLastCalledWith(expect.objectContaining({ visible: false }));

  await user.click(screen.getByRole("tab", { name: "Map" }));
  expect(mapMounts).toHaveBeenCalledTimes(1);
  expect(mapProps).toHaveBeenLastCalledWith(expect.objectContaining({ visible: true }));
});

it("gives the route map a real height rather than leaving it at the collapsed default", async () => {
  show("route-analysis");
  await userEvent.setup().click(screen.getByRole("tab", { name: "Map" }));
  expect(mapProps).toHaveBeenLastCalledWith(expect.objectContaining({ height: 420 }));
});
