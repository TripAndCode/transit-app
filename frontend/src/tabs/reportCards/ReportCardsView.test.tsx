import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderWithProviders } from "../../test/renderWithProviders";
import * as api from "../../components/reportCard/reportCardApi";
import type { RouteCard } from "../../components/reportCard/reportCardApi";
import { ReportCardsView } from "./ReportCardsView";

const base: RouteCard = {
  route_code: "50",
  month: "2026-09",
  frequency: "low",
  grade: "C",
  not_graded: null,
  observations: 2000,
  floor: 100,
  trips_observed: 1000,
  planned_trips: 1000,
  coverage_pct: 100,
  on_time_ci_low: 83,
  on_time_ci_high: 87,
  missing: [],
  subscores: [
    { metric: "on_time_pct", value: 85, ci_low: 83, ci_high: 87, letter: "B", graded: true, missing: null },
    { metric: "ewt_min", value: null, ci_low: null, ci_high: null, letter: null, graded: false, missing: "not_measured" },
  ],
  raise_to: [],
};

function open(cards: RouteCard[]) {
  vi.spyOn(api, "useReportCards").mockReturnValue({
    data: { month: "2026-09", latest_month: "2026-10", cards, rules: {} as never, definition: { value_basis: "feed_prediction" } as never },
    error: null,
    refetch: vi.fn(),
  } as never);
  const router = createMemoryRouter([{ path: "agencies/:agencyId/routes", element: <ReportCardsView /> }], {
    initialEntries: ["/agencies/9/routes?by=report_cards"],
  });
  renderWithProviders(<RouterProvider router={router} />);
}

describe("ReportCardsView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists each route's grade or why it has none, linking to its card", async () => {
    open([base, { ...base, route_code: "51", grade: null, not_graded: "coverage_low", coverage_pct: 41 }]);
    expect(await screen.findByText("N/A: observation coverage 41.0%")).toBeInTheDocument();
    const link = screen.getAllByRole("link").find((a) => a.getAttribute("href")?.includes("/routes/50"));
    expect(link?.getAttribute("href")).toContain("card_month=2026-09");
    const [graded, gated] = screen.getAllByRole("row").slice(1);
    expect(graded).toHaveTextContent("85.0%");
    expect(graded).toHaveTextContent("C");
    expect(gated).toHaveTextContent("N/A");
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toContain("report-cards?month=2026-09&format=csv");
  });

  it("says when no route has a card that month", async () => {
    open([]);
    expect(await screen.findByText(/No route was observed or planned/)).toBeInTheDocument();
  });
});
