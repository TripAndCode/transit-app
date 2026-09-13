import { beforeEach, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { ReportsHomeTab } from "../../tabs/ReportsHomeTab";
import { RouteAnalysisTab } from "../../tabs/RouteAnalysisTab";
import { readAnalyses, saveAnalysis } from "./savedAnalyses";
import { downloadCsv } from "./csv";
import { useReport } from "../../api/hooks";

vi.mock("./AnalysisMap", () => ({ AnalysisMap: () => <div>Map</div> }));
vi.mock("./csv", () => ({ downloadCsv: vi.fn() }));
vi.mock("../../api/hooks", () => ({
  useRoutes: () => ({ data: [
    { route_id: "a", route_code: "101", route_long_name: "Coast", route_short_name: "1", trip_headsigns: [] },
    { route_id: "b", route_code: "999", route_long_name: "Coast", route_short_name: "9", trip_headsigns: [] },
  ], isPending: false }),
  useAgencies: () => ({ data: [{ agency_id: 1, agency_name: "Test Agency" }] }),
  useRouteShape: () => ({ data: { route: "101", geometry: null, stops: [{ stop_id: "A", stop_sequence: 1, stop_name: "Station A", lon: 140, lat: 40, avg_min: 2, samples: 5 }] }, isPending: false }),
  useReport: vi.fn((_id, type) => ({ data: type ? { report_type: type, definition: {}, rows: type === "trend" ? [{ days: [{ date: "2026-09-07", avg_min: 2, samples: 4 }] }] : [["101", null, 2, 1, 3, 4]] } : undefined, isPending: false })),
}));
const ctx = { from: "2026-09-07", to: "2026-09-12", dow: "weekday" as const, time_band: "morning" as const, service: "all" as const, routes: ["101"] };
function show(tab: "route-analysis" | "reports", search = "") {
  return render(<MemoryRouter initialEntries={[`/agencies/1/${tab}?from=${ctx.from}&to=${ctx.to}&routes=101&dow=weekday&time_band=morning${search}`]}>
    <Routes><Route path="/agencies/:agencyId/route-analysis" element={<RouteAnalysisTab />} /><Route path="/agencies/:agencyId/reports" element={<ReportsHomeTab />} /></Routes>
  </MemoryRouter>);
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
  show("reports");
  const user = userEvent.setup();
  await user.selectOptions(screen.getByRole("combobox", { name: "Service pattern" }), "999");
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
