import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
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
// Same line as routeA once the leading pattern number is stripped.
const routeA2: Route = { route_id: "2", route_code: "A2", route_long_name: "5 Line One", route_short_name: null, trip_headsigns: [] };
const routeB: Route = { route_id: "3", route_code: "B1", route_long_name: "Line Two", route_short_name: null, trip_headsigns: [] };

describe("PatternFilters", () => {
  afterEach(() => vi.restoreAllMocks());

  it("offers every route, plus the stale code itself, when the selected code belongs to no line", () => {
    // A deep link or stale selection whose route_code has since dropped out
    // of the agency's routes: selectedGroup returns "", so no line is
    // selected and the pattern select falls back to the full route list.
    mockRoutes([routeA]);
    renderWithProviders(<PatternFilters agencyId={1} codes={["ghost-code"]} onChange={vi.fn()} />);
    expect(screen.getByLabelText("Route")).toHaveValue("");
    expect(screen.getByLabelText("Service pattern")).toHaveValue("ghost-code");
    expect(screen.getByText("A1 · Line One")).toBeInTheDocument();
  });

  it("narrows the patterns to the selected line, and a line change selects every code under that line", () => {
    mockRoutes([routeA, routeA2, routeB]);
    const onChange = vi.fn();
    renderWithProviders(<PatternFilters agencyId={1} codes={["A1"]} onChange={onChange} />);
    expect(screen.getByLabelText("Route")).toHaveValue("Line One");
    expect(screen.getByText("A1 · Line One")).toBeInTheDocument();
    expect(screen.getByText("A2 · 5 Line One")).toBeInTheDocument();
    expect(screen.queryByText("B1 · Line Two")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Route"), { target: { value: "Line Two" } });
    expect(onChange).toHaveBeenLastCalledWith(["B1"]);
    fireEvent.change(screen.getByLabelText("Route"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith([]);
  });
});
