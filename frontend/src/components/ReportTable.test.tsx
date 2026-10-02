import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
    // Variants of a line can share a label, so the code follows it, muted.
    expect(screen.getByText("T50")).toBeInTheDocument();
    expect(screen.getByText("39061")).toHaveClass("route-label__code");
  });

  it("falls back to the bare route_code when no static route matches (data gap, not a crash)", () => {
    // A route_code with no matching
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

describe("ReportTable inline bars", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("draws the bar in its own track beside the value, never under the number", () => {
    mockRoutes([]);
    renderTable([["33101", "平日", 6.0, 5.5, 9.8, 436]]);
    const value = screen.getByText("6.0");
    const track = screen.getByTestId("bar-track");
    expect(track).not.toContainElement(value);
    expect(track.style.position).not.toBe("absolute");
    expect(value.closest("td")).toContainElement(track);
  });
});

describe("ReportTable units and route names", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the unit once in the header and leaves the cells to the figures", () => {
    mockRoutes([]);
    renderTable([["33101", "平日", 6.0, 5.5, 9.8, 436]]);
    expect(screen.getByRole("columnheader", { name: "Avg (min)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Median (min)" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Samples" })).toBeInTheDocument();
    expect(screen.getByText("5.5")).toBeInTheDocument();
    expect(screen.queryByText(/\d\s?min$/)).toBeNull();
  });

  it("keeps the route column readable when the table scrolls sideways", () => {
    mockRoutes([]);
    renderTable([["33101", "平日", 6.0, 5.5, 9.8, 436]]);
    const cell = screen.getByText("Route 33101").closest("td") as HTMLElement;
    expect(cell.style.position).toBe("sticky");
    expect(cell.style.wordBreak).toBe("keep-all");
  });
});

describe("ReportTable route links", () => {
  afterEach(() => vi.restoreAllMocks());

  it("opens a row's route from its name, keeping the scope but not the report", () => {
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/routes?report=ranking&from=2026-09-01&dow=weekday"]}>
        <Routes>
          <Route path="/agencies/:agencyId/routes" element={<ReportTable reportType="ranking" rows={[["39061", "平日", 5.2, 3.1, 8.4, 120]]} />} />
        </Routes>
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: /T50/ });
    expect(link).toHaveAttribute("href", "/agencies/1/routes/39061?from=2026-09-01&dow=weekday");
    expect(link.closest("tr")).toHaveClass("report-row--link");
  });
});

describe("ReportTable service column", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names services in the UI's language, and marks an untranslated one as Japanese", () => {
    mockRoutes([]);
    renderTable([
      ["39061", "秋彼岸", 5.2, 3.1, 8.4, 120], // i18n-ignore: GTFS service name
      ["39061", "お盆臨時　20日", 4.0, 3.0, 7.0, 90], // i18n-ignore: GTFS service name
    ]);
    expect(screen.getByText("Autumn equinox (Higan)")).toBeInTheDocument();
    expect(screen.getByText("お盆臨時 20日")).toHaveAttribute("lang", "ja"); // i18n-ignore: GTFS service name
  });
});

describe("ReportTable on a phone", () => {
  beforeEach(() => {
    vi.spyOn(window, "matchMedia").mockReturnValue({
      matches: true,
      media: "(max-width: 640px)",
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    } as unknown as MediaQueryList);
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
  });
  afterEach(() => vi.restoreAllMocks());

  it("lists each row as one readable item instead of a seven-column table", () => {
    renderTable([["39061", "平日", 5.2, 3.1, 8.4, 120]]);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const [item] = screen.getAllByRole("listitem");
    for (const text of ["1", "T50", "39061", "Avg", "5.2 min", "Weekday", "Median", "3.1 min", "p90", "8.4 min", "Samples", "120"]) {
      expect(within(item).getByText(text)).toBeInTheDocument();
    }
  });

  it("names no route for a report without a route column", () => {
    renderTable([[92.3, 5.2, 120, 100, 95, 95.0]], "council_summary");
    const [item] = screen.getAllByRole("listitem");
    expect(within(item).queryByText(/Route/)).not.toBeInTheDocument();
    expect(within(item).getAllByText("On-time %")).toHaveLength(1);
    expect(within(item).getByText("Planned trips")).toBeInTheDocument();
  });

  it("titles a certificate row by its own route column, not the agency in column 0", () => {
    renderTable([["Agency A", "39061", "平日", "2026-09-01", "08:00", "08:12", 720]], "delay_certificate");
    const [item] = screen.getAllByRole("listitem");
    expect(item.querySelector(".report-cards__route")).toHaveTextContent("39061");
    expect(within(item).getAllByText("Agency A")).toHaveLength(1);
    expect(within(item).getAllByText("39061")).toHaveLength(1);
  });

  it("leaves out a field with nothing to show, rather than a bare label", () => {
    renderTable(
      [
        ["39061", "平日", 0.92, 1.2, 300, false],
        ["39061", "平日", 0.5, 4.0, 8, true],
      ],
      "on_time",
    );
    const [confident, wide] = screen.getAllByRole("listitem");
    expect(within(confident).queryByText("Confidence")).not.toBeInTheDocument();
    expect(within(wide).getByText("Confidence")).toBeInTheDocument();
    expect(within(wide).getByText("wide range")).toBeInTheDocument();
  });

  it("shows 25 rows first and the rest on request", async () => {
    const rows = Array.from({ length: 30 }, (_, i) => ["39061", "平日", 5 - i / 10, 3, 8, 100 + i]);
    renderTable(rows);
    expect(screen.getAllByRole("listitem")).toHaveLength(25);
    await userEvent.click(screen.getByRole("button", { name: "Show 5 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(30);
  });
});
