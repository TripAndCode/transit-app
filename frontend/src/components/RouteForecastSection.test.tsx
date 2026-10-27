import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RouteForecastSection } from "./RouteForecastSection";
import * as hooks from "../api/hooks";
import { BAND_ORDER, type ForecastDay, type ForecastOverview } from "../api/types";
import { calmDelayColor } from "../styles/tokens";

function fullGrid(populate: { dow: number; band: string; v: number; n?: number }[] = []) {
  const set = new Map(populate.map((p) => [`${p.dow}-${p.band}`, p]));
  const grid = [];
  for (let dow = 1; dow <= 7; dow++) {
    for (const band of BAND_ORDER) {
      const p = set.get(`${dow}-${band}`);
      grid.push({
        dow,
        band,
        expected_avg_min: p ? p.v : null,
        samples: p ? (p.n ?? 200) : 0,
        low_confidence: p ? (p.n ?? 200) < 30 : false,
        source: "baseline" as const,
      });
    }
  }
  return grid;
}

function overview(partial: Partial<ForecastOverview> = {}): ForecastOverview {
  return {
    grid: fullGrid([{ dow: 1, band: "midday", v: 6.8, n: 250 }]),
    worst: { dow: 1, band: "midday", expected_avg_min: 6.8, samples: 250, source: "baseline" },
    routes: [
      { route_code: "100", route_name: "Main Line", expected_avg_min: 6.8, samples: 250, low_confidence: false },
      { route_code: "200", route_name: "Side Line", expected_avg_min: 9.9, samples: 5, low_confidence: true },
    ],
    forecast_days: [],
    history_from: "2026-07-12",
    history_to: "2026-10-23",
    recent_from: "2026-10-17",
    recent_to: "2026-10-23",
    disclaimer: "test disclaimer",
    ...partial,
  };
}

function heatmap(populate: { d: number; h: number; v?: number; n?: number }[] = []) {
  const set = new Map(populate.map((p) => [`${p.d}-${p.h}`, p]));
  const cells = [];
  for (let d = 1; d <= 7; d++) {
    for (let h = 0; h < 24; h++) {
      const p = set.get(`${d}-${h}`);
      const populated = p || h === 12;
      const samples = p?.n ?? (populated ? 300 : 0);
      cells.push({
        dow: d,
        hour: h,
        expected_avg_min: populated ? (p?.v ?? 5 + d) : null,
        samples,
        low_confidence: samples > 0 && samples < 30,
      });
    }
  }
  return {
    route: "100",
    cells,
    forecast_days: [] as ForecastDay[],
    history_from: "2026-07-12",
    history_to: "2026-10-23",
    disclaimer: "test disclaimer",
  };
}

function renderSection(ov: ForecastOverview | undefined = overview(), initialRoute = "", hm = heatmap()) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
  vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: ov, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: hm, isPending: false, error: null, refetch: vi.fn() } as never);
  const initialEntries = [`/agencies/1/analysis/route_forecast${initialRoute ? `?routes=${initialRoute}` : ""}`];
  renderWithProviders(
    <MemoryRouter initialEntries={initialEntries}>
      <RouteForecastSection aid={1} />
    </MemoryRouter>,
  );
}

describe("RouteForecastSection", () => {
  it("renders the agency landing with worst window, 35-cell grid, and ranked routes", () => {
    renderSection();
    expect(screen.getByTestId("worst-headline")).toBeInTheDocument();
    expect(screen.getByText(/When to watch out/i)).toBeInTheDocument();
    expect(screen.getAllByTestId("ov-band-cell").length).toBe(35);
    const ranked = within(screen.getByTestId("fc-overview-routes"));
    expect(ranked.getByText("Main Line")).toBeInTheDocument();
    expect(ranked.getByText("Side Line")).toBeInTheDocument();
    expect(screen.getAllByTestId("ranked-route").length).toBe(2);
    expect(screen.getByText("test disclaimer")).toBeInTheDocument();
  });

  it("renders a sparkline for a route with recent_daily data, none for a route without", () => {
    renderSection(
      overview({
        routes: [
          { route_code: "100", route_name: "Main Line", expected_avg_min: 6.8, samples: 250, low_confidence: false, recent_daily: [2.0, 4.0, 6.0] },
          { route_code: "200", route_name: "Side Line", expected_avg_min: 9.9, samples: 5, low_confidence: true },
        ],
      }),
    );
    const rows = screen.getAllByTestId("ranked-route");
    expect(rows).toHaveLength(2);
    // InlineSparkline's <svg> carries aria-hidden (it's decorative next to the
    // numeric value), so it must be queried with { hidden: true } — otherwise
    // testing-library's accessibility-tree filtering excludes it from getByRole.
    expect(within(rows[0]).getByRole("img", { hidden: true })).toBeInTheDocument();
    expect(within(rows[1]).queryByRole("img", { hidden: true })).not.toBeInTheDocument();
    // The sparkline's grid cell must stay present even when InlineSparkline
    // itself renders null (row 1 has no recent_daily) — otherwise CSS grid
    // auto-placement shifts the trailing delay-number into the sparkline's
    // fixed-width track, misaligning it against rows that do show one.
    expect(rows[0].children).toHaveLength(4);
    expect(rows[1].children).toHaveLength(4);
  });

  it("shows the empty-state message when the agency has no data", () => {
    renderSection(overview({ grid: fullGrid([]), worst: null, routes: [] }));
    expect(screen.getByText(/No measurements yet/i)).toBeInTheDocument();
  });

  it("titles a chosen route's grid for that route, and the agency's for all routes", () => {
    renderSection(overview(), "100");
    expect(within(screen.getByTestId("fc-detail-bandgrid")).getByText("Day × time of day (this route)")).toBeInTheDocument();
    expect(screen.queryByText("Day × time of day (all routes)")).toBeNull();
  });

  it("flips the full-grid toggle to a hide label with aria-expanded once the grid is open", () => {
    renderSection(overview(), "100");
    const toggle = screen.getByRole("button", { name: "Show day × hour detail" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    const open = screen.getByRole("button", { name: "Hide day × hour detail" });
    expect(open).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("fc-detail-fullgrid")).toBeInTheDocument();
  });

  it("shows the per-route detail when exactly one route is already selected via the URL", () => {
    renderSection(overview(), "100");
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
    expect(screen.getByTestId("fc-detail-bandgrid")).toBeInTheDocument();
    expect(screen.queryByText(/Back to overview/i)).not.toBeInTheDocument();
  });

  it("keeps a route's worst window when its bands carry ML forecasts, counting their baseline samples", () => {
    vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isLoading: false } as never);
    vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: overview(), isPending: false, error: null, refetch: vi.fn() } as never);
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: ml, isPending: false, error: null, refetch: vi.fn() } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/analysis/route_forecast?routes=100"]}>
        <RouteForecastSection aid={1} />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
  });

  it("says how late the worst window runs in words, not as a signed figure", () => {
    renderSection();
    const headline = screen.getByTestId("worst-headline");
    expect(headline).toHaveTextContent("avg 6.8 min late");
    expect(headline).not.toHaveTextContent("+");
  });

  it("says an early-running worst window is early, not late", () => {
    renderSection(overview({ worst: { dow: 1, band: "midday", expected_avg_min: -0.4, samples: 250, source: "baseline" } }));
    expect(screen.getByTestId("worst-headline")).toHaveTextContent("avg 0.4 min early");
  });

  it("words a worst window the ML model set as expected, not as an average", () => {
    renderSection(overview({ worst: { dow: 1, band: "midday", expected_avg_min: 6.8, samples: 250, source: "ml" } }));
    const headline = screen.getByTestId("worst-headline");
    expect(headline).toHaveTextContent("6.8 min late expected");
    expect(headline).not.toHaveTextContent("avg");
  });

  it("words a route's ML-forecast worst window as expected too", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    renderSection(overview(), "100", ml);
    expect(screen.getByTestId("detail-worst")).toHaveTextContent(/12\.0 min late expected/);
    expect(screen.getByTestId("detail-worst")).not.toHaveTextContent("avg");
  });

  it("says how late a route's worst window runs in words too", () => {
    renderSection(overview(), "100");
    expect(screen.getByTestId("detail-worst")).toHaveTextContent(/avg 12\.0 min late/);
    expect(screen.getByTestId("detail-worst")).not.toHaveTextContent("+");
  });

  it("keeps a route's code out of a ranked row, in its tooltip", () => {
    renderSection(
      overview({
        routes: [
          { route_code: "C12a", route_name: "Loop Line", expected_avg_min: 6.8, samples: 250, low_confidence: false },
          { route_code: "C12b", route_name: "Loop Line", expected_avg_min: 5.1, samples: 250, low_confidence: false },
        ],
      }),
    );
    const [first, second] = screen.getAllByTestId("ranked-route");
    expect(first).not.toHaveTextContent("C12a");
    expect(first.querySelector(".route-label")).toHaveAttribute("title", "C12a");
    expect(second.querySelector(".route-label")).toHaveAttribute("title", "C12b");
  });

  it("reads the payload's route name like a short name until the static routes load", () => {
    renderSection(
      overview({
        routes: [
          { route_code: "1471981638", route_name: "70-3", expected_avg_min: 6.8, samples: 250, low_confidence: false },
          { route_code: "1", route_name: "Line 10", expected_avg_min: 5.1, samples: 250, low_confidence: false },
        ],
      }),
    );
    const [coded, named] = screen.getAllByTestId("ranked-route");
    expect(coded.querySelector(".route-label__plate")).toHaveTextContent("70-3");
    expect(coded).not.toHaveTextContent("1471981638");
    expect(named.querySelector(".route-label__plate")).toBeNull();
    expect(within(named).getByText("Line 10")).toBeInTheDocument();
  });

  it("says what the small line beside each route shows, and over which days", () => {
    const routes = overview().routes.map((r, i) => (i === 0 ? { ...r, recent_daily: [2.0, 4.0] } : r));
    renderSection(overview({ routes }));
    expect(screen.getByRole("region", { name: "Past measurements (Jul 12 – Oct 23)" })).toHaveTextContent(
      "The small line traces its daily average on the days with data over Oct 17 – Oct 23.",
    );
  });

  it("leaves the small line unexplained when no shown route has two days to draw it from", () => {
    const routes = overview().routes.map((r) => ({ ...r, recent_daily: [3.0] }));
    renderSection(overview({ routes }));
    expect(screen.getByRole("region", { name: "Past measurements (Jul 12 – Oct 23)" })).not.toHaveTextContent(
      "The small line traces",
    );
  });

  it("heads the route list as past measurements over its window, apart from the forecast and its caveat", () => {
    renderSection();
    const history = screen.getByRole("region", { name: "Past measurements (Jul 12 – Oct 23)" });
    expect(within(history).getByTestId("fc-overview-routes")).toHaveTextContent("Routes that ran late");
    expect(within(history).queryByTestId("fc-overview-grid")).toBeNull();
    expect(within(history).queryByText("test disclaimer")).toBeNull();
  });

  it("says the grid is a typical week when no forecast model covers it", () => {
    renderSection();
    expect(screen.getByTestId("fc-period")).toHaveTextContent("Not a forecast for particular dates");
  });

  it("dates each forecast row and a forecast worst window, leaving the past-average rows as weekdays", () => {
    const mlGrid = fullGrid([
      { dow: 1, band: "midday", v: 2.0 },
      { dow: 5, band: "evening", v: 5.8 },
    ]).map((c) => (c.dow === 5 && c.expected_avg_min != null ? { ...c, source: "ml" as const } : c));
    renderSection(
      overview({
        grid: mlGrid,
        forecast_days: [{ dow: 5, service_date: "2026-10-30" }],
        worst: { dow: 5, band: "evening", expected_avg_min: 5.8, samples: 250, source: "ml" },
      }),
    );
    expect(screen.getByTestId("fc-period")).toHaveTextContent("Forecast for Fri, Oct 30.");
    expect(screen.getByTestId("worst-headline")).toHaveTextContent("Fri 10/30 Evening runs latest");
    const rows = within(screen.getByTestId("fc-overview-grid")).getAllByRole("rowheader").map((h) => h.textContent);
    expect(rows).toEqual(["Mon", "Tue", "Wed", "Thu", "Fri 10/30", "Sat", "Sun"]);
  });

  it("keeps a past-average worst window undated, even on a weekday the forecast covers", () => {
    renderSection(
      overview({
        forecast_days: [{ dow: 1, service_date: "2026-10-26" }],
        worst: { dow: 1, band: "midday", expected_avg_min: 6.8, samples: 250, source: "baseline" },
      }),
    );
    expect(screen.getByTestId("worst-headline")).toHaveTextContent("Mon Midday runs latest");
    expect(screen.getByTestId("worst-headline")).not.toHaveTextContent("10/26");
  });

  it("colours each route's bar by its own delay, so a route running seven minutes late is never the calm end", () => {
    renderSection(
      overview({
        routes: [
          { route_code: "100", route_name: "Main Line", expected_avg_min: 10.6, samples: 250, low_confidence: false },
          { route_code: "300", route_name: "Third Line", expected_avg_min: 7.3, samples: 250, low_confidence: false },
        ],
      }),
    );
    const fill = (row: HTMLElement) => (row.children[1].firstElementChild as HTMLElement).style.background;
    const [worse, better] = screen.getAllByTestId("ranked-route");
    expect(fill(better)).toBe(calmDelayColor(7.3));
    expect(fill(better)).toBe(fill(worse));
    expect(fill(better)).not.toBe(calmDelayColor(0));
  });

  it("dates a chosen route's full day × hour rows and its by-day bars where its forecast covers them", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.dow === 2 && c.expected_avg_min != null ? { ...c, source: "ml" as const } : c));
    ml.forecast_days = [{ dow: 2, service_date: "2026-10-27" }];
    renderSection(overview(), "100", ml);

    const dayBars = screen.getAllByTestId("dow-bar");
    expect(dayBars[1]).toHaveAccessibleName(/^Tue 10\/27 · /);
    expect(dayBars[0]).toHaveAccessibleName(/^Mon · /);
    // The bar axis has a narrow column per day, so the day of the month sits under the weekday.
    expect(within(screen.getByTestId("fc-card-dow")).getByText("27")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Show day × hour detail" }));
    const rows = within(screen.getByTestId("fc-detail-fullgrid")).getAllByRole("rowheader").map((h) => h.textContent);
    expect(rows).toEqual(["Mon", "Tue 10/27", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  });

  it("dates a chosen route's forecast rows from that route's own forecast", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.dow === 2 && c.expected_avg_min != null ? { ...c, source: "ml" as const } : c));
    ml.forecast_days = [{ dow: 2, service_date: "2026-10-27" }];
    renderSection(overview({ forecast_days: [{ dow: 5, service_date: "2026-10-30" }] }), "100", ml);
    expect(screen.getByTestId("fc-period")).toHaveTextContent("Forecast for Tue, Oct 27.");
    const rows = within(screen.getByTestId("fc-detail-bandgrid")).getAllByRole("rowheader").map((h) => h.textContent);
    expect(rows).toEqual(["Mon", "Tue 10/27", "Wed", "Thu", "Fri", "Sat", "Sun"]);
  });

  it("picks a route from its own control, and goes back to all routes the same way", () => {
    renderSection();
    const picker = screen.getByRole("combobox", { name: "Route" });
    expect(picker).toHaveValue("");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual(["All routes", "Main Line", "Side Line"]);
    fireEvent.change(picker, { target: { value: "100" } });
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Route" }), { target: { value: "" } });
    expect(screen.getByTestId("worst-headline")).toBeInTheDocument();
  });

  it("tells alike names apart in the picker by their code only as a last resort, and keeps a focused route without a forecast", () => {
    renderSection(
      overview({
        routes: [
          { route_code: "C12a", route_name: "Loop Line", expected_avg_min: 6.8, samples: 250, low_confidence: false },
          { route_code: "C12b", route_name: "Loop Line", expected_avg_min: 5.1, samples: 250, low_confidence: false },
        ],
      }),
      "900",
    );
    const picker = screen.getByRole("combobox", { name: "Route" });
    expect(picker).toHaveValue("900");
    expect(within(picker).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "All routes",
      "Loop Line (C12a)",
      "Loop Line (C12b)",
      "900",
    ]);
  });

  it("drilling into a route from the ranked list updates the URL's routes param (shared range-context)", () => {
    renderSection();
    fireEvent.click(within(screen.getByTestId("fc-overview-routes")).getByText("Main Line"));
    // RouteForecastSection reads focusedRoute from the same URL-backed context
    // it just wrote to — re-rendering should now show the per-route detail.
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
    expect(screen.getByTestId("fc-detail-bandgrid")).toBeInTheDocument();
  });

  it("expands the full day×hour grid on demand in the detail view", () => {
    renderSection(overview(), "100");
    expect(screen.queryByTestId("fc-detail-fullgrid")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    expect(screen.getByTestId("fc-detail-fullgrid")).toBeInTheDocument();
    expect(screen.getAllByTestId("hm-cell").length).toBe(7); // hour-12 only, 7 days
  });

  it("opens the by-day detail modal with stats + disclaimer", () => {
    renderSection(overview(), "100");
    fireEvent.click(within(screen.getByTestId("fc-card-dow")).getByRole("button", { name: /^Expand/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("test disclaimer")).toBeInTheDocument();
  });

  it("labels the detail's count as past observations when the forecast comes from the ML model", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    renderSection(overview(), "100", ml);
    fireEvent.click(within(screen.getByTestId("fc-card-dow")).getByRole("button", { name: /^Expand/ }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Past observations")).toBeInTheDocument();
    expect(within(dialog).queryByText("Measurements")).toBeNull();
    // The route figure pools the model's forecasts, so it is not called an average.
    expect(within(dialog).getByText("Route expected")).toBeInTheDocument();
    expect(within(dialog).queryByText("Route avg")).toBeNull();
  });

  it("keeps calling the detail's count measurements for a baseline forecast", () => {
    renderSection(overview(), "100");
    fireEvent.click(within(screen.getByTestId("fc-card-dow")).getByRole("button", { name: /^Expand/ }));
    expect(within(screen.getByRole("dialog")).getByText("Measurements")).toBeInTheDocument();
    expect(within(screen.getByRole("dialog")).getByText("Route avg")).toBeInTheDocument();
  });

  it("marks a route's forecast as the ML model's when its cells come from it", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    renderSection(overview(), "100", ml);
    expect(screen.getAllByTestId("fc-ml-chip")).toHaveLength(1);
    expect(screen.getByTestId("fc-ml-chip")).toHaveTextContent("ML forecast");
  });

  it("links the ML mark to the model evaluation page, carrying the agency it was opened from", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    renderSection(overview(), "100", ml);
    expect(screen.getByTestId("fc-ml-chip")).toHaveAttribute("href", "/model?from=1");
  });

  it("shows no ML mark on a baseline-only route or agency landing", () => {
    renderSection(overview(), "100");
    expect(screen.queryByTestId("fc-ml-chip")).toBeNull();
  });

  it("marks the agency landing when any band comes from the ML model", () => {
    const ov = overview();
    ov.grid = ov.grid.map((c) => (c.expected_avg_min == null ? c : { ...c, source: "ml" as const }));
    renderSection(ov);
    expect(screen.getByTestId("fc-ml-chip")).toHaveTextContent("ML forecast");
  });

  it("names an ML cell's source in its label, and leaves a baseline cell's alone", () => {
    const ml = heatmap();
    ml.cells = ml.cells.map((c) => (c.dow === 1 && c.hour === 12 ? { ...c, source: "ml" as const } : c));
    renderSection(overview(), "100", ml);
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    const labels = screen.getAllByTestId("hm-cell").map((c) => c.getAttribute("aria-label") ?? "");
    expect(labels.filter((l) => l.includes("ML forecast"))).toHaveLength(1);
  });

  it("opens a summary card from an explicit Expand button, once, and leaves the card itself non-interactive", () => {
    renderSection(overview(), "100");
    const card = screen.getByTestId("fc-card-dow");
    // A role=button card would make its bar grid presentational for assistive tech.
    expect(card).not.toHaveAttribute("role");
    expect(card).not.toHaveAttribute("tabindex");
    const expand = within(card).getByRole("button", { name: /^Expand By day$/ });
    fireEvent.click(expand);
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });

  it("does not open the modal when Enter or Space is pressed on a bar inside the card", () => {
    renderSection(overview(), "100");
    const bar = screen.getAllByTestId("dow-bar")[0];
    fireEvent.keyDown(bar, { key: "Enter" });
    fireEvent.keyDown(bar, { key: " " });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows visible amber badge on low-confidence heatmap cells", () => {
    vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: overview(), isPending: false, error: null, refetch: vi.fn() } as never);
    vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({
      data: heatmap([{ d: 1, h: 9, v: 4, n: 5 }]),
      isPending: false,
      error: null,
      refetch: vi.fn(),
    } as never);
    renderWithProviders(
      <MemoryRouter initialEntries={["/agencies/1/analysis/route_forecast?routes=100"]}>
        <RouteForecastSection aid={1} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    const lowConfBadges = screen.queryAllByTestId("hm-cell-lowconf");
    const visible = lowConfBadges.filter((el) => !el.hasAttribute("hidden"));
    expect(visible.length).toBeGreaterThan(0);
  });

  it("names a heatmap cell once, through the tooltip that opens on focus", () => {
    renderSection(overview(), "100");
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    const cell = screen.getAllByTestId("hm-cell")[0];

    // Native title= tooltips were replaced by the shared, keyboard-reachable Tooltip.
    expect(cell).not.toHaveAttribute("title");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    // fireEvent.focusIn/focusOut, not .focus()/.blur() or fireEvent.focus/blur:
    // React's onFocus/onBlur are implemented on the bubbling focusin/focusout
    // events, which a bare non-bubbling native "focus" event never fires.
    fireEvent.focusIn(cell);
    const tooltip = screen.getByRole("tooltip");
    expect(tooltip).toHaveTextContent(cell.getAttribute("aria-label") ?? "");
    // One channel, not three: the cell's name is the tooltip's text, and no
    // live region repeats it behind them.
    expect(screen.queryByTestId("hm-readout")).toBeNull();
    expect(document.querySelectorAll('[aria-live="polite"]')).toHaveLength(0);

    fireEvent.focusOut(cell);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("folds the low-sample warning into the cell's own name instead of a nested tooltip", () => {
    renderSection(overview(), "100");
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    const badge = screen.queryAllByTestId("hm-cell-lowconf")[0];
    if (!badge) return;

    // A tooltip nested inside the cell's own would open alongside it, because
    // focus events bubble; the glyph is decoration and its text lives in the
    // cell's label.
    expect(badge).toHaveAttribute("aria-hidden", "true");
    expect(badge).not.toHaveAttribute("tabindex");
    const cell = badge.closest('[role="gridcell"]');
    expect(cell?.getAttribute("aria-label")).toMatch(/·.*·/);
  });

  it("is one tab stop per grid, with the arrow keys moving inside it", () => {
    renderSection(overview(), "100");
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    const grid = screen.getAllByRole("grid").find((g) => g.querySelectorAll('[role="gridcell"]').length === 168)!;
    const cells = [...grid.querySelectorAll('[role="gridcell"]')];

    // 7x24 cells, exactly one of them reachable by Tab.
    expect(cells).toHaveLength(168);
    expect(cells.filter((c) => c.getAttribute("tabindex") === "0")).toHaveLength(1);
    expect(cells[0]).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(cells[1]).toHaveAttribute("tabindex", "0");
    expect(cells[0]).toHaveAttribute("tabindex", "-1");

    fireEvent.keyDown(grid, { key: "ArrowDown" });
    expect(cells[25]).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(grid, { key: "Home" });
    expect(cells[24]).toHaveAttribute("tabindex", "0");

    fireEvent.keyDown(grid, { key: "End" });
    expect(cells[47]).toHaveAttribute("tabindex", "0");

    // The row edges hold rather than wrapping onto a neighbouring day.
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(cells[47]).toHaveAttribute("tabindex", "0");
  });

  it("gives the margin bars one tab stop too", () => {
    renderSection(overview(), "100");
    const bars = screen.getAllByTestId("dow-bar");

    expect(bars.filter((b) => b.getAttribute("tabindex") === "0")).toHaveLength(1);
    expect(bars[0]).not.toHaveAttribute("title");

    fireEvent.focusIn(bars[0]);
    expect(screen.getByRole("tooltip")).toHaveTextContent(bars[0].getAttribute("aria-label") ?? "");
    expect(screen.queryByTestId("dow-bar-readout")).toBeNull();
  });

});
