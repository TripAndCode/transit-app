import { describe, it, expect, vi } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { ForecastTab } from "./ForecastTab";
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

function heatmap() {
  const cells = [];
  for (let d = 1; d <= 7; d++) {
    for (let h = 0; h < 24; h++) {
      const populated = h === 12;
      cells.push({ dow: d, hour: h, expected_avg_min: populated ? 5 + d : null, samples: populated ? 300 : 0, low_confidence: false });
    }
  }
  return { route: "100", cells, disclaimer: "test disclaimer" };
}

function renderTab(ov: ForecastOverview | undefined = overview()) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isPending: false, isLoading: false, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useForecastOverview").mockReturnValue({ data: ov, isPending: false, error: null, refetch: vi.fn() } as never);
  vi.spyOn(hooks, "useForecastHeatmap").mockReturnValue({ data: heatmap(), isPending: false, error: null, refetch: vi.fn() } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/forecast"]}>
      <Routes>
        <Route path="/agencies/:agencyId/forecast" element={<ForecastTab />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ForecastTab", () => {
  it("renders the agency landing with worst window, 35-cell grid, and ranked routes", () => {
    renderTab();
    expect(screen.getByTestId("worst-headline")).toBeInTheDocument();
    expect(screen.getByText(/When to watch out/i)).toBeInTheDocument();
    expect(screen.getAllByTestId("ov-band-cell").length).toBe(35);
    expect(screen.getByText("Main Line")).toBeInTheDocument();
    expect(screen.getByText("Side Line")).toBeInTheDocument();
    expect(screen.getAllByTestId("ranked-route").length).toBe(2);
  });

  it("shows the empty-state message when the agency has no data", () => {
    renderTab(overview({ grid: fullGrid([]), worst: null, routes: [] }));
    expect(screen.getByText(/No measurements yet/i)).toBeInTheDocument();
  });

  it("drills into a route from the ranked list and back", () => {
    renderTab();
    expect(screen.queryByText(/Back to overview/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("Main Line"));
    // detail view: back button + per-route band grid + worst sentence
    expect(screen.getByText(/Back to overview/i)).toBeInTheDocument();
    expect(screen.getByTestId("detail-worst")).toBeInTheDocument();
    expect(screen.getByTestId("fc-detail-bandgrid")).toBeInTheDocument();
    // back to the agency landing
    fireEvent.click(screen.getByText(/Back to overview/i));
    expect(screen.getByTestId("worst-headline")).toBeInTheDocument();
  });

  it("expands the full day×hour grid on demand in the detail view", () => {
    renderTab();
    fireEvent.click(screen.getByText("Main Line"));
    expect(screen.queryByTestId("fc-detail-fullgrid")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Show day . hour detail/i));
    expect(screen.getByTestId("fc-detail-fullgrid")).toBeInTheDocument();
    expect(screen.getAllByTestId("hm-cell").length).toBe(7); // hour-12 only, 7 days
  });

  it("opens the by-day detail modal with stats + disclaimer", () => {
    renderTab();
    fireEvent.click(screen.getByText("Main Line"));
    fireEvent.click(screen.getByTestId("fc-card-dow"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("test disclaimer")).toBeInTheDocument();
  });
});
