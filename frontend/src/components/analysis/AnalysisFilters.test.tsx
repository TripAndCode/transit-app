import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PatternFilters } from "./AnalysisFilters";
import * as hooks from "../../api/hooks";
import type { Route } from "../../api/types";

function mockRoutes(routes: Route[]) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: routes,
    isPending: false,
    error: null,
    refetch: vi.fn(),
  } as never);
}

const routeA: Route = { route_id: "1", route_code: "A1", route_long_name: "Line One", route_short_name: null, trip_headsigns: [] };

describe("PatternFilters", () => {
  afterEach(() => vi.restoreAllMocks());

  it("falls back to the full route list instead of crashing when the selected code no longer belongs to any group", () => {
    // Simulates a deep link or stale selection whose route_code has since
    // dropped out of the agency's routes -- selectedGroup then returns "",
    // exercising the `options = group ? ... : routes` fallback branch.
    mockRoutes([routeA]);
    renderWithProviders(<PatternFilters agencyId={1} codes={["ghost-code"]} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Route")).toHaveValue("");
    // The stale code is still offered as an option (existing behavior) and
    // the real route is present too -- neither lookup threw.
    expect(screen.getByLabelText("Service pattern")).toHaveValue("ghost-code");
    expect(screen.getByText("A1 · Line One")).toBeInTheDocument();
  });
});
