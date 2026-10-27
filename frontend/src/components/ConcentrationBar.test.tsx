import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import "../i18n";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { ConcentrationBar } from "./ConcentrationBar";
import type { OverviewConcentration } from "../api/types";

const concentration: OverviewConcentration = {
  top_routes: [
    { route_code: "A", route_short_name: null, share_pct: 30 },
    { route_code: "B", route_short_name: null, share_pct: 20 },
  ],
  rest_share_pct: 50,
  rest_route_count: 4,
};

const PERIOD = "Sep 24 – Oct 23";

function renderBar(ui: ReactElement) {
  return renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/pulse"]}>
      <Routes>
        <Route path="/agencies/:agencyId/pulse" element={ui} />
      </Routes>
    </MemoryRouter>,
  );
}

function mockRoutes() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: [
      {
        route_id: "1471981638",
        route_code: "1471981638",
        route_short_name: "70-3",
        route_long_name: "あさひが丘線",
        trip_headsigns: [],
        origin_name: "広島バスセンター",
        destination_name: "あさひが丘下",
      },
      { route_id: "360868713", route_code: "360868713", route_short_name: "3", route_long_name: "広島駅－観音線", trip_headsigns: [] },
    ],
    isLoading: false,
  } as never);
}

describe("ConcentrationBar", () => {
  afterEach(() => vi.restoreAllMocks());

  it("draws the largest share at full width and the rest in proportion to it", () => {
    const { container } = renderBar(
      <ConcentrationBar
        periodRange={PERIOD}
        concentration={{
          top_routes: [
            { route_code: "A", route_short_name: null, share_pct: 3.9 },
            { route_code: "B", route_short_name: null, share_pct: 1.3 },
          ],
          rest_share_pct: 94.8,
          rest_route_count: 40,
        }}
      />,
    );
    const fills = Array.from(container.querySelectorAll<HTMLElement>(".ov-pareto-fill")).map((f) => parseFloat(f.style.width));
    expect(fills[0]).toBe(100);
    expect(fills[1]).toBeCloseTo(33.33, 1);
  });

  it("hands each fill its rank opacity as a custom property, so the stylesheet's hover rule can still change it", () => {
    const { container } = renderBar(<ConcentrationBar periodRange={PERIOD} concentration={concentration} />);
    const fills = [...container.querySelectorAll<HTMLElement>(".ov-pareto-fill")];
    expect(fills).toHaveLength(2);
    // An inline `opacity` outranks every class rule, which would leave the
    // hover affordance in overview.css with nothing to act on.
    expect(fills.map((f) => f.style.opacity)).toEqual(["", ""]);
    expect(fills.map((f) => f.style.getPropertyValue("--rank-opacity"))).toEqual(["1", "0.8"]);
  });

  it("labels each route with the shared route label, never its route_id", () => {
    mockRoutes();
    const { container } = renderBar(
      <ConcentrationBar
        periodRange={PERIOD}
        concentration={{
          top_routes: [
            { route_code: "360868713", route_short_name: "3", share_pct: 6.2 },
            { route_code: "1471981638", route_short_name: "70-3", share_pct: 4.4 },
          ],
          rest_share_pct: 89.4,
          rest_route_count: 155,
        }}
      />,
    );
    const labels = [...container.querySelectorAll(".ov-pareto-label")];
    expect(labels[0]).toHaveTextContent(/^3 広島駅－観音線$/);
    expect(labels[1].querySelector(".route-label__plate")).toHaveTextContent("70-3");
    expect(labels[1].querySelector(".route-label__ends")).toHaveTextContent("広島バスセンター → あさひが丘下");
    expect(container).not.toHaveTextContent("360868713");
    expect(container).not.toHaveTextContent("1471981638");
  });

  it("names a route by the payload's short name until the static routes load", () => {
    const { container } = renderBar(
      <ConcentrationBar periodRange={PERIOD} concentration={{ top_routes: [{ route_code: "1471981638", route_short_name: "70-3", share_pct: 4 }, ...concentration.top_routes], rest_share_pct: 46 }} />,
    );
    expect(container.querySelector(".ov-pareto-label .route-label__plate")).toHaveTextContent("70-3");
    expect(container).not.toHaveTextContent("1471981638");
  });

  it("names the one observed route by its label in the spotlight sentence", () => {
    mockRoutes();
    const { container } = renderBar(
      <ConcentrationBar periodRange={PERIOD} concentration={{ top_routes: [{ route_code: "1471981638", route_short_name: "70-3", share_pct: 100 }], rest_share_pct: 0 }} />,
    );
    expect(container.querySelector(".ov-conc-spotlight")).toHaveTextContent(
      "70-3 あさひが丘線 accounts for 100% — only 1 route counted.",
    );
    expect(container).not.toHaveTextContent("1471981638");
  });

  it("states every share as one of the delay of the routes it counts, and says how many and when", () => {
    const { container } = renderBar(<ConcentrationBar periodRange={PERIOD} concentration={concentration} limit={20} variant="modal" />);
    // Two listed routes and four more the API only counts.
    expect(container.querySelector(".ov-conc-headline")).toHaveTextContent(
      "Top 2 routes carry 50% of the delay of the 6 routes observed in Sep 24 – Oct 23.",
    );
    expect(container.querySelector(".ov-lorenz-caption")?.textContent).toMatch(
      /^Top 20% of routes carry \d+% of the delay among the routes counted\.$/,
    );
    expect(container.querySelector(".ov-modal-explainer")).toHaveTextContent(
      "Route codes the current timetable no longer runs, and routes whose feed always reports zero delay, are not counted.",
    );
  });

  it("leaves the rest as what the headline's share leaves, so the two add up to 100", () => {
    // Five shown shares, each rounded on its own, sum to 19.8 (headline 20%);
    // the API's rest after its top 20 plus the 15 unshown would print 80.3%.
    const shares = [5.0, 4.1, 4.0, 3.5, 3.2, ...Array.from({ length: 15 }, () => 2.0)];
    const { container } = renderBar(
      <ConcentrationBar
        periodRange={PERIOD}
        concentration={{
          top_routes: shares.map((share_pct, i) => ({ route_code: `R${i}`, route_short_name: null, share_pct })),
          rest_share_pct: 50.3,
          rest_route_count: 154,
        }}
      />,
    );
    expect(container.querySelector(".ov-conc-headline")).toHaveTextContent(/carry 20% of the delay of the 174 routes/);
    expect(container.querySelector(".ov-pareto-rest")).toHaveTextContent("Remaining 169 routes: 80%");
  });
});
