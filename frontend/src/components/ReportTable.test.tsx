import { useState } from "react";
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

type TableProps = Partial<Pick<Parameters<typeof ReportTable>[0], "minSamples" | "sortBy" | "sortDirection">>;

function renderTable(rows: unknown[][], reportType = "route_table", props: TableProps = {}) {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/analysis"]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/analysis"
          element={<ReportTable reportType={reportType} rows={rows} {...props} />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

/** A Routes table row: (route_code, avg_min, on_time_pct, late5_pct,
 *  late5_count, samples, low_confidence, margin_pp). */
function tableRow(code: string, avg: number, extra: Partial<{ onTime: number; share: number; count: number; samples: number; wide: boolean; margin: number }> = {}) {
  const { onTime = 60.0, share = 10.0, count = 12, samples = 120, wide = false, margin = 4.0 } = extra;
  return [code, avg, onTime, share, count, samples, wide, margin];
}

/** A pending-nav provider whose navigation stays pending until "settle". */
function TestNav({ go, children }: { go: (to: string) => void; children: React.ReactNode }) {
  const [pendingTo, setPendingTo] = useState<string | null>(null);
  return (
    <NavPendingContext
      value={{
        pendingTo,
        go: (to) => {
          go(to);
          setPendingTo(to);
        },
      }}
    >
      <button type="button" onClick={() => setPendingTo(null)}>
        settle
      </button>
      {children}
    </NavPendingContext>
  );
}

function renderWithNav(rows: unknown[][], go: (to: string) => void, reportType = "route_table") {
  return renderWithProviders(
    <TestNav go={go}>
      <MemoryRouter initialEntries={["/agencies/1/analysis?from=2026-09-01"]}>
        <Routes>
          <Route path="/agencies/:agencyId/analysis" element={<ReportTable reportType={reportType} rows={rows} />} />
        </Routes>
      </MemoryRouter>
    </TestNav>,
  );
}

describe("ReportTable route link travel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });
  const rows = [tableRow("3", 4.2), tableRow("12", 3.0)];

  it("a plain click opens the route as a screen navigation, and only the clicked label travels into the title", async () => {
    mockRoutes([]);
    const go = vi.fn();
    renderWithNav(rows, go);
    await userEvent.click(screen.getByRole("link", { name: /Route 3/ }));
    expect(go).toHaveBeenCalledWith("/agencies/1/routes/3?from=2026-09-01");
    const shared = screen.getAllByTestId("route-title-transition");
    expect(shared).toHaveLength(1);
    expect(shared[0]).toHaveTextContent("Route 3");
    // The travelling link is one box, chevron included; the rest wrap as text.
    expect(shared[0].firstElementChild).toHaveClass("report-route-link", "report-route-link--travelling");
    expect(document.querySelectorAll(".report-route-link--travelling")).toHaveLength(1);
  });

  it("holds the name only while that row's navigation is pending, so a superseded one leaves nothing named", async () => {
    mockRoutes([]);
    renderWithNav(rows, vi.fn());
    await userEvent.click(screen.getByRole("link", { name: /Route 3/ }));
    expect(screen.getByTestId("route-title-transition")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "settle" }));
    expect(screen.queryByTestId("route-title-transition")).toBeNull();
  });

  it("a route on several rows still lends its name to only the clicked row", async () => {
    mockRoutes([]);
    renderWithNav(
      [
        ["Agency A", "3", "平日", "2026-09-01", "08:00", "08:12", 720],
        ["Agency A", "3", "平日", "2026-09-01", "09:00", "09:10", 600],
      ],
      vi.fn(),
      "delay_certificate",
    );
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

  it("labels the compare ranking's weekend column as the weekend/holiday day type it counts", () => {
    renderTable([["39061", 5.2, 3.1, 2.1, 2.1]], "compare_ranking");
    expect(screen.getByRole("columnheader", { name: /^Weekend\/Holiday\b/ })).toBeInTheDocument();
  });

  it("renders a matched route by its plate, line name and terminals, keeping the route_id in the tooltip", () => {
    mockRoutes([
      {
        route_id: "1471981638",
        route_short_name: "70-3",
        route_long_name: "あさひが丘線",
        route_code: "1471981638",
        trip_headsigns: [],
        origin_name: "広島バスセンター",
        destination_name: "あさひが丘下",
      },
    ]);
    renderTable([["1471981638", null, "平日", 5.2, 120]], "dow_weekday");
    expect(screen.getByText("70-3")).toHaveClass("route-label__plate");
    expect(screen.getByText("あさひが丘線")).toHaveClass("route-label__line");
    expect(screen.getByText("広島バスセンター → あさひが丘下")).toBeInTheDocument();
    expect(screen.queryByText("1471981638")).toBeNull();
    expect(screen.getByText("70-3").closest(".route-label")).toHaveAttribute("title", "1471981638");
  });

  it("falls back to the bare route_code when no static route matches (data gap, not a crash)", () => {
    // A route_code with no matching
    // static_routes row (via api/routers/static.py's regexp_replace(route_id)
    // extraction) must still render something readable rather than throwing —
    // "Route <code>" is the documented, accepted fallback for a genuine gap.
    mockRoutes([
      { route_id: "T50線(39061)", route_short_name: "T50", route_long_name: "石江・新城線", route_code: "39061", trip_headsigns: [] },
    ]);
    renderTable([tableRow("53011", 5.2)]);
    expect(screen.getByText("Route 53011")).toBeInTheDocument();
  });

  // One line per row: where the route runs moves into the tooltip, so a long
  // pair of terminals never makes its row taller than the rest.
  it("names a Routes table row by its plate and line on one line, where it runs in the tooltip", () => {
    mockRoutes([
      {
        route_id: "1471981638",
        route_short_name: "70-3",
        route_long_name: "あさひが丘線",
        route_code: "1471981638",
        trip_headsigns: [],
        origin_name: "広島バスセンター",
        destination_name: "あさひが丘下",
      },
    ]);
    renderTable([tableRow("1471981638", 5.2)]);
    expect(screen.getByText("70-3")).toHaveClass("route-label__plate");
    expect(screen.getByText("あさひが丘線")).toHaveClass("route-label__line");
    expect(screen.queryByText("広島バスセンター → あさひが丘下")).toBeNull();
    expect(screen.getByText("70-3").closest(".route-label")).toHaveAttribute(
      "title",
      expect.stringContaining("広島バスセンター → あさひが丘下"),
    );
  });
});

describe("ReportTable on-time margin", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // The flag measures the percentage's 95% interval, which narrows toward
  // 0% and 100%, so it is named for the margin and never for the count.
  it("marks a percentage with a wide interval by its margin, naming the points on focus", async () => {
    mockRoutes([]);
    renderTable([tableRow("39061", 0.5, { onTime: 59.8, samples: 189, wide: true, margin: 7.0 })]);
    const badge = screen.getByText("wide margin");
    expect(badge).toHaveClass("caveat-badge");
    expect(badge.closest("td")).toHaveTextContent(/^59\.8%/);
    expect(screen.queryByRole("columnheader", { name: "Margin" })).not.toBeInTheDocument();
    expect(screen.queryByText("few data")).not.toBeInTheDocument();
    // Past the row's route link, the badge is the next stop on the keyboard.
    await userEvent.tab();
    await userEvent.tab();
    expect(badge).toHaveFocus();
    expect(screen.getByRole("tooltip")).toHaveTextContent("Margin of error ±7.0 points (95% interval)");
  });

  it("renders no badge for a percentage with a narrow interval, however few its observations", () => {
    mockRoutes([]);
    renderTable([tableRow("39061", 0.0, { onTime: 100.0, samples: 112, margin: 1.7 })]);
    expect(screen.queryByText("wide margin")).not.toBeInTheDocument();
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
    renderTable([tableRow("33101", 6.0)], "route_table", { sortBy: "avg" });
    const value = screen.getByText("6.0");
    const track = screen.getByTestId("bar-track");
    expect(track).not.toContainElement(value);
    expect(track.style.position).not.toBe("absolute");
    expect(value.closest("td")).toContainElement(track);
  });

  it("draws an on-time bar against 100%, so a list of low rates never fills its bars", () => {
    mockRoutes([]);
    renderTable([tableRow("A", 4.0, { onTime: 11.3 }), tableRow("B", 3.0, { onTime: 22.6 })], "route_table", { sortBy: "on_time" });
    const fills = screen.getAllByTestId("bar-track").map((track) => (track.firstElementChild as HTMLElement).style.width);
    expect(fills).toEqual(["11.3%", "22.6%"]);
  });
});

describe("ReportTable units and route names", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("names the unit once in the header and leaves the cells to the figures", () => {
    mockRoutes([]);
    renderTable([tableRow("33101", 6.0, { samples: 436 })]);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "#",
      "Route",
      "Avg (min)",
      "On-time %",
      "Share over 5 min",
      "Over 5 min (count)",
      "Samples",
    ]);
    expect(screen.getByText("6.0")).toBeInTheDocument();
    expect(within(screen.getAllByRole("row")[1]).queryByText(/\d\s?min$/)).toBeNull();
  });

  it("keeps the route column readable when the table scrolls sideways", () => {
    mockRoutes([]);
    renderTable([tableRow("33101", 6.0)]);
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
      <MemoryRouter initialEntries={["/agencies/1/routes?sort=late5_share&order=best&from=2026-09-01&dow=weekday"]}>
        <Routes>
          <Route path="/agencies/:agencyId/routes" element={<ReportTable reportType="route_table" rows={[tableRow("39061", 5.2)]} />} />
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
      <MemoryRouter initialEntries={["/agencies/1/routes"]}>
        <Routes>
          <Route path="/agencies/:agencyId/routes" element={<ReportTable reportType="route_table" rows={[tableRow("21111", 0.9)]} />} />
        </Routes>
      </MemoryRouter>,
    );
    const chevron = screen.getByRole("link", { name: /C12/ }).querySelector(".report-route-link__chevron");
    expect(chevron?.textContent).toBe("\u00a0›");
  });
});

describe("ReportTable service column", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names a certificate's services in the UI's language, and marks an untranslated one as Japanese", () => {
    mockRoutes([]);
    renderTable(
      [
        ["Agency A", "39061", "秋彼岸", "2026-09-22", "08:00", "08:12", 720], // i18n-ignore: GTFS service name
        ["Agency A", "39061", "お盆臨時　20日", "2026-08-20", "08:00", "08:12", 720], // i18n-ignore: GTFS service name
      ],
      "delay_certificate",
    );
    expect(screen.getByText("Autumn equinox (Higan)")).toBeInTheDocument();
    expect(screen.getByText("お盆臨時 20日")).toHaveAttribute("lang", "ja"); // i18n-ignore: GTFS service name
    expect(screen.getByRole("columnheader", { name: "Service" })).toBeInTheDocument();
    expect(screen.getByText(/Service: the timetable the trip ran on/)).toBeInTheDocument();
  });

  // One row per route over all its observations: the row's service slot is
  // the active filter, the same on every row, which the scope already names.
  it.each([
    ["route_table", tableRow("39061", 5.2)],
    ["dow_weekday", ["39061", null, "平日", 3.4, 120]],
    ["dow_weekend", ["39061", null, "土日祝", 3.4, 120]],
  ])("gives the per-route %s report no service column or note", (reportType, row) => {
    mockRoutes([]);
    renderTable([row], reportType);
    expect(screen.queryByRole("columnheader", { name: "Service" })).toBeNull();
    expect(screen.queryByText("Weekday")).toBeNull();
    expect(screen.queryByText(/Service: the timetable the trip ran on/)).toBeNull();
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
    renderTable([tableRow("39061", 5.2, { onTime: 61.5, share: 9.8, count: 12, samples: 120 })], "route_table", { sortBy: "avg" });
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    const [item] = screen.getAllByRole("listitem");
    for (const text of ["1", "T50", "石江・新城線", "Avg", "5.2 min", "On-time %", "61.5%", "Share over 5 min", "9.8%", "Over 5 min (count)", "12", "Samples", "120"]) {
      expect(within(item).getByText(text)).toBeInTheDocument();
    }
    expect(within(item).queryByText("39061")).toBeNull();
  });

  it("heads each card with the figure the table is sorted by", () => {
    renderTable([tableRow("39061", 5.2, { share: 9.8 })], "route_table", { sortBy: "late5_share" });
    const [item] = screen.getAllByRole("listitem");
    expect(item.querySelector(".report-cards__headline")).toHaveTextContent("Share over 5 min 9.8%");
    expect(item.querySelector(".report-cards__meta")).toHaveTextContent("Avg 5.2 min");
  });

  it("titles a certificate row by its own route column, not the agency in column 0", () => {
    renderTable([["Agency A", "39061", "平日", "2026-09-01", "08:00", "08:12", 720]], "delay_certificate");
    const [item] = screen.getAllByRole("listitem");
    expect(item.querySelector(".report-cards__route")).toHaveTextContent(/^T50 石江・新城線/);
    expect(within(item).getAllByText("Agency A")).toHaveLength(1);
    expect(within(item).getAllByText("T50")).toHaveLength(1);
  });

  it("marks a wide margin on the on-time figure itself, and nothing on a confident one", () => {
    renderTable(
      [tableRow("39061", 1.2, { onTime: 92.0, samples: 300, margin: 1.2 }), tableRow("39061", 4.0, { onTime: 50.0, samples: 8, wide: true, margin: 9.4 })],
      "route_table",
      { sortBy: "avg" },
    );
    const [confident, wide] = screen.getAllByRole("listitem");
    expect(within(confident).queryByText("wide margin")).not.toBeInTheDocument();
    expect(within(wide).getByText("wide margin").closest(".report-cards__field")).toHaveTextContent(/^On-time % 50\.0%/);
  });

  it("shows a missing service as a dash, not as the word null", () => {
    renderTable([["Agency A", "39061", null, "2026-09-01", "08:00", "08:12", 720]], "delay_certificate");
    const [item] = screen.getAllByRole("listitem");
    expect(item).not.toHaveTextContent(/null/);
    expect(within(item).getByText("—")).toBeInTheDocument();
  });

  it("shows 25 rows first and the rest on request", async () => {
    const rows = Array.from({ length: 30 }, (_, i) => tableRow("39061", 5 - i / 10, { samples: 100 + i }));
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
    renderTable(
      [
        ["33101", null, "平日", 6.0, 436],
        ["33102", null, "平日", 3.2, 512],
      ],
      "dow_weekday",
    );
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

});

describe("ReportTable thinly observed rows", () => {
  beforeEach(() => mockRoutes([]));
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("marks a row observed fewer times than the reliable floor with a few-data badge", () => {
    renderTable([tableRow("SPARSE", 9.1, { samples: 30 }), tableRow("SOLID", 2.0, { samples: 150 })], "route_table", {
      minSamples: 100,
    });
    const [sparseRow, solidRow] = screen.getAllByRole("row").slice(1);
    expect(within(sparseRow).getByText("few data")).toHaveClass("caveat-badge");
    expect(within(solidRow).queryByText("few data")).not.toBeInTheDocument();
  });

  it("marks nothing when the report states no floor", () => {
    renderTable([tableRow("SPARSE", 9.1, { samples: 30 })]);
    expect(screen.queryByText("few data")).not.toBeInTheDocument();
  });
});

describe("ReportTable sorted by one figure", () => {
  beforeEach(() => mockRoutes([]));
  afterEach(() => vi.restoreAllMocks());

  it("sets the sorted column apart: its header says the order, its figures carry the weight and the only bar", () => {
    renderTable([tableRow("A", 6.0, { share: 40.0 }), tableRow("B", 3.0, { share: 20.0 })], "route_table", {
      sortBy: "late5_share",
      sortDirection: "descending",
    });
    const header = screen.getByRole("columnheader", { name: "Share over 5 min" });
    expect(header).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Avg (min)" })).not.toHaveAttribute("aria-sort");
    expect(screen.getByText("40.0%").closest("td")).toHaveClass("report-cell--basis");
    expect(screen.getByText("6.0").closest("td")).not.toHaveClass("report-cell--basis");
    expect(screen.getAllByTestId("bar-track")).toHaveLength(2);
    expect(screen.getByText("40.0%").closest("td")).toContainElement(screen.getAllByTestId("bar-track")[0]);
  });

  it("keeps every header on one line", () => {
    renderTable([tableRow("A", 6.0)], "route_table", { sortBy: "avg" });
    for (const header of screen.getAllByRole("columnheader")) expect(header.style.whiteSpace).toBe("nowrap");
  });

  // Most of a busy agency's routes average past the severe threshold, so a
  // marker on each flags nothing.
  it("marks no row of the Routes table as severe", () => {
    renderTable([tableRow("A", 9.0), tableRow("B", 6.0)], "route_table", { sortBy: "avg" });
    expect(screen.queryByTestId("delay-marker")).toBeNull();
  });

  it("colours a delay bar by the figure it prints, so equal figures share a colour", () => {
    renderTable([tableRow("A", 1.46), tableRow("B", 1.54), tableRow("C", 1.44)], "route_table", { sortBy: "avg" });
    const fill = (text: string) =>
      (within(screen.getAllByText(text)[0].closest("td")!).getByTestId("bar-track").firstElementChild as HTMLElement).style.background;
    const [a, b] = screen.getAllByText("1.5").map((el) => (within(el.closest("td")!).getByTestId("bar-track").firstElementChild as HTMLElement).style.background);
    expect(a).toBe(b);
    expect(fill("1.4")).not.toBe(a);
  });
});
