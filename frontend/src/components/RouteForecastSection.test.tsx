import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { RouteForecastSection } from "./RouteForecastSection";
import * as hooks from "../api/hooks";
import { BAND_ORDER, type ForecastOverview } from "../api/types";

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
      });
    }
  }
  return grid;
}

function overview(partial: Partial<ForecastOverview> = {}): ForecastOverview {
  return {
    grid: fullGrid([{ dow: 1, band: "midday", v: 6.8, n: 250 }]),
    worst: { dow: 1, band: "midday", expected_avg_min: 6.8, samples: 250 },
    routes: [
      { route_code: "100", route_name: "Main Line", expected_avg_min: 6.8, samples: 250, low_confidence: false },
      { route_code: "200", route_name: "Side Line", expected_avg_min: 9.9, samples: 5, low_confidence: true },
    ],
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
  return { route: "100", cells, disclaimer: "test disclaimer" };
}

function renderSection(ov: ForecastOverview | undefined = overview(), initialRoute = "") {
  vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: ov, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: heatmap(), isPending: false, error: null, refetch: vi.fn() } as never);
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
    expect(screen.getByText("Main Line")).toBeInTheDocument();
    expect(screen.getByText("Side Line")).toBeInTheDocument();
    expect(screen.getAllByTestId("ranked-route").length).toBe(2);
    expect(screen.getByText("test disclaimer")).toBeInTheDocument();
  });

  it("shows the empty-state message when the agency has no data", () => {
    renderSection(overview({ grid: fullGrid([]), worst: null, routes: [] }));
    expect(screen.getByText(/No measurements yet/i)).toBeInTheDocument();
  });

  it("shows the per-route detail when exactly one route is already selected via the URL", () => {
    renderSection(overview(), "100");
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
    expect(screen.getByTestId("fc-detail-bandgrid")).toBeInTheDocument();
    // no in-view "back" button — clearing the route is done via the shared Filters bar, not tested here
    expect(screen.queryByText(/Back to overview/i)).not.toBeInTheDocument();
  });

  it("drilling into a route from the ranked list updates the URL's routes param (shared range-context)", () => {
    renderSection();
    fireEvent.click(screen.getByText("Main Line"));
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
    fireEvent.click(screen.getByTestId("fc-card-dow"));
    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(within(dialog).getByText("test disclaimer")).toBeInTheDocument();
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
});
