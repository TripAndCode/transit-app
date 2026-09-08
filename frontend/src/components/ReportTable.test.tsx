import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ReportTable } from "./ReportTable";
import * as hooks from "../api/hooks";
import type { Route as RouteRecord } from "../api/types";

function mockRoutes(data: RouteRecord[]) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data, isLoading: false } as never);
}

function renderTable(rows: unknown[][], reportType = "ranking") {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/analysis"]}>
      <Routes>
        <Route path="/agencies/:agencyId/analysis" element={<ReportTable reportType={reportType} rows={rows} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ReportTable route column", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a matched route's name alongside its code", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    renderTable([["39061", "平日", 5.2, 3.1, 8.4, 120]]);
    expect(screen.getByText("T50 (39061)")).toBeInTheDocument();
  });

  it("falls back to the bare route_code when no static route matches (data gap, not a crash)", () => {
    // Regression guard for the reported bug: a route_code with no matching
    // static_routes row (via api/routers/static.py's regexp_replace(route_id)
    // extraction) must still render something readable rather than throwing —
    // "Route <code>" is the documented, accepted fallback for a genuine gap.
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    renderTable([["53011", "平日", 5.2, 3.1, 8.4, 120]]);
    expect(screen.getByText("Route 53011")).toBeInTheDocument();
  });
});

describe("ReportTable on_time confidence column", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a caveat marker for a low-confidence percentage", () => {
    mockRoutes([]);
    renderTable([["39061", "平日", 80.0, 0.5, 25, true]], "on_time");
    expect(screen.getByText("wide range")).toBeInTheDocument();
  });

  it("renders no caveat marker for a confident percentage", () => {
    mockRoutes([]);
    renderTable([["39061", "平日", 90.0, 0.5, 300, false]], "on_time");
    expect(screen.queryByText("wide range")).not.toBeInTheDocument();
  });
});

describe("ReportTable council_summary/delay_certificate schemas", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the pooled council_summary row", () => {
    mockRoutes([]);
    renderTable([[71.4, 3.2, 35, 4, 3, 75.0]], "council_summary");
    expect(screen.getByText("71.4%")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument();
  });

  it("renders a delay_certificate row without crashing on the un-enriched route column", () => {
    mockRoutes([]);
    renderTable([["Test Agency", "RCERT", "平日", "2026-06-20", "10:00:00", "10:06:40", 400]], "delay_certificate");
    expect(screen.getByText("Test Agency")).toBeInTheDocument();
    expect(screen.getByText("400")).toBeInTheDocument();
  });
});
