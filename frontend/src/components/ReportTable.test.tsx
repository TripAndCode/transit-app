import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ReportTable } from "./ReportTable";
import * as hooks from "../api/hooks";
import type { Route as RouteRecord } from "../api/types";
import { NavPendingContext } from "./navPendingContext";
import { stubReducedMotion } from "../test/reducedMotion";

// jsdom runs no view transitions, so the shared-element wrapper is swapped for
// a marker that shows which label wears the title's name.
vi.mock("./RouteTitleTransition", () => ({
  RouteTitleTransition: ({ children }: { children: React.ReactNode }) => <span data-testid="route-title-transition">{children}</span>,
}));

function mockRoutes(data: RouteRecord[]) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data, isLoading: false } as never);
}

function renderTable(rows: unknown[][], reportType = "ranking", minSamples?: number) {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/analysis"]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/analysis"
          element={<ReportTable reportType={reportType} rows={rows} minSamples={minSamples} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

function renderWithNav(rows: unknown[][], go: (to: string) => void) {
  return renderWithProviders(
    <NavPendingContext value={{ pendingTo: null, go }}>
      <MemoryRouter initialEntries={["/agencies/1/analysis?from=2026-09-01"]}>
        <Routes>
          <Route path="/agencies/:agencyId/analysis" element={<ReportTable reportType="ranking" rows={rows} />} />
        </Routes>
      </MemoryRouter>
    </NavPendingContext>,
  );
}

describe("ReportTable route link travel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });
  const rows = [
    ["3", "weekday", 4.2, 3.1, 7.0, 120],
    ["12", "weekday", 3.0, 2.1, 5.0, 120],
  ];

  it("a plain click opens the route as a screen navigation, and only the clicked label travels into the title", async () => {
    mockRoutes([]);
    const go = vi.fn();
    renderWithNav(rows, go);
    await userEvent.click(screen.getByRole("link", { name: /Route 3/ }));
    expect(go).toHaveBeenCalledWith("/agencies/1/routes/3?from=2026-09-01");
    const shared = screen.getAllByTestId("route-title-transition");
    expect(shared).toHaveLength(1);
    expect(shared[0]).toHaveTextContent("Route 3");
  });

  it("a route listed once per service still lends its name to only the clicked row", async () => {
    mockRoutes([]);
    renderWithNav([["3", "weekday", 4.2, 3.1, 7.0, 120], ["3", "weekend", 3.9, 2.8, 6.0, 90]], vi.fn());
    await userEvent.click(screen.getAllByRole("link", { name: /Route 3/ })[1]);
    expect(screen.getAllByTestId("route-title-transition")).toHaveLength(1);
    expect(screen.getAllByRole("row")[2]).toContainElement(screen.getByTestId("route-title-transition"));
  });

  it("under reduced motion the click still navigates, but nothing travels", async () => {
    mockRoutes([]);
    stubReducedMotion();
    const go = vi.fn();
    renderWithNav(rows, go);
    await userEvent.click(screen.getByRole("link", { name: /Route 3/ }));
    expect(go).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("route-title-transition")).toBeNull();
  });

  it("a modified click stays the browser's: no screen navigation, nothing named", async () => {
    mockRoutes([]);
    const go = vi.fn();
    renderWithNav(rows, go);
    const user = userEvent.setup();
    await user.keyboard("{Control>}");
    await user.click(screen.getByRole("link", { name: /Route 3/ }));
    await user.keyboard("{/Control}");
    expect(go).not.toHaveBeenCalled();
    expect(screen.queryByTestId("route-title-transition")).toBeNull();
  });
});

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

  it("marks a low-confidence percentage with a muted few-data badge", () => {
    mockRoutes([]);
    renderTable([["39061", "平日", 80.0, 0.5, 25, true]], "on_time");
    expect(screen.getByText("few data")).toHaveClass("caveat-badge");
    expect(screen.getByRole("columnheader", { name: "Data" })).toBeInTheDocument();
  });

  it("renders no badge for a confident percentage", () => {
    mockRoutes([]);
    renderTable([["39061", "平日", 90.0, 0.5, 300, false]], "on_time");
    expect(screen.queryByText("few data")).not.toBeInTheDocument();
  });
});

describe("ReportTable delay_certificate schema", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders a delay_certificate row without crashing on the un-enriched route column", () => {
    mockRoutes([]);
    renderTable([["Test Agency", "RCERT", "平日", "2026-06-20", "10:00:00", "10:06:40", 400]], "delay_certificate");
    expect(screen.getByText("Test Agency")).toBeInTheDocument();
    // 400 seconds reads as minutes and seconds, like every other delay.
    expect(screen.getByText("6 min 40 s")).toBeInTheDocument();
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

  it("keeps the chevron on the route code's line, so a narrow cell never wraps it alone", () => {
    mockRoutes([
      { route_id: "C12線(21111)", route_short_name: "C12", route_long_name: "造道・八重田線", route_code: "21111", trip_headsigns: [] },
    ]);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/routes?report=ranking"]}>
        <Routes>
          <Route path="/agencies/:agencyId/routes" element={<ReportTable reportType="ranking" rows={[["21111", "平日", 0.9, 0.9, 2.0, 245]]} />} />
        </Routes>
      </MemoryRouter>,
    );
    const chevron = screen.getByRole("link", { name: /C12/ }).querySelector(".report-route-link__chevron");
    expect(chevron?.textContent).toBe("\u00a0›");
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
    expect(within(confident).queryByText("Data")).not.toBeInTheDocument();
    expect(within(wide).getByText("Data")).toBeInTheDocument();
    expect(within(wide).getByText("few data")).toBeInTheDocument();
  });

  it("shows a missing service as a dash, not as the word null", () => {
    renderTable([["39061", null, 5.2, 3.1, 8.4, 120]]);
    const [item] = screen.getAllByRole("listitem");
    expect(item).not.toHaveTextContent(/null/);
    expect(within(item).getByText("—")).toBeInTheDocument();
  });

  it("shows 25 rows first and the rest on request", async () => {
    const rows = Array.from({ length: 30 }, (_, i) => ["39061", "平日", 5 - i / 10, 3, 8, 100 + i]);
    renderTable(rows);
    expect(screen.getAllByRole("listitem")).toHaveLength(25);
    await userEvent.click(screen.getByRole("button", { name: "Show 5 more" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(30);
  });
});

describe("ReportTable delay figures", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("prints delay figures in the text colour, and marks only the severe ones", () => {
    mockRoutes([]);
    renderTable([
      ["33101", "平日", 6.0, 5.5, 9.8, 436],
      ["33102", "平日", 3.2, 3.0, 6.1, 512],
    ]);
    const severe = screen.getByText("6.0");
    expect(severe.style.color).toBe("");
    expect(within(severe.closest("td")!).getByTestId("delay-marker")).toBeInTheDocument();
    const moderate = screen.getByText("3.2");
    expect(moderate.style.color).toBe("");
    expect(within(moderate.closest("td")!).queryByTestId("delay-marker")).toBeNull();
  });
});

describe("ReportTable day-group reports", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("leaves out the day column the report's own title already states", () => {
    mockRoutes([]);
    renderTable([["33101", "平日", "平日", 3.4, 120]], "dow_weekday");
    expect(screen.queryByRole("columnheader", { name: "Day" })).toBeNull();
    expect(screen.getByText("3.4")).toBeInTheDocument();
  });

  it("says under the table what the service column means", () => {
    mockRoutes([]);
    renderTable([["33101", "平日", "平日", 3.4, 120]], "dow_weekday");
    expect(screen.getByText(/Service: the timetable the trip ran on/)).toBeInTheDocument();
  });
});

describe("ReportTable thinly observed rows", () => {
  beforeEach(() => mockRoutes([]));
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("marks a row observed fewer times than the reliable floor with a few-data badge", () => {
    renderTable(
      [
        ["SPARSE", "平日", 9.1, 8.0, 12.0, 30], // i18n-ignore: GTFS service name
        ["SOLID", "平日", 2.0, 1.5, 4.0, 150], // i18n-ignore: GTFS service name
      ],
      "ranking",
      100,
    );
    const [sparseRow, solidRow] = screen.getAllByRole("row").slice(1);
    expect(within(sparseRow).getByText("few data")).toHaveClass("caveat-badge");
    expect(within(solidRow).queryByText("few data")).not.toBeInTheDocument();
  });

  it("marks nothing when the report states no floor", () => {
    renderTable([["SPARSE", "平日", 9.1, 8.0, 12.0, 30]]); // i18n-ignore: GTFS service name
    expect(screen.queryByText("few data")).not.toBeInTheDocument();
  });
});
