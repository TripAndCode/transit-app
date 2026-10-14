import { describe, it, expect, vi, afterEach } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
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
    const patterns = within(screen.getByLabelText("Service pattern"));
    expect(patterns.getByRole("option", { name: "Line One" })).toBeInTheDocument();
    expect(patterns.getByRole("option", { name: "ghost-code" })).toBeInTheDocument();
  });

  it("narrows the patterns to the selected line, and a line change selects every code under that line", () => {
    mockRoutes([routeA, routeA2, routeB]);
    const onChange = vi.fn();
    renderWithProviders(<PatternFilters agencyId={1} codes={["A1"]} onChange={onChange} />);
    expect(screen.getByLabelText("Route")).toHaveValue("Line One");
    const patterns = within(screen.getByLabelText("Service pattern"));
    expect(patterns.getByRole("option", { name: "Line One" })).toBeInTheDocument();
    expect(patterns.getByRole("option", { name: "5 Line One" })).toBeInTheDocument();
    expect(patterns.queryByRole("option", { name: "Line Two" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Route"), { target: { value: "Line Two" } });
    expect(onChange).toHaveBeenLastCalledWith(["B1"]);
    fireEvent.change(screen.getByLabelText("Route"), { target: { value: "" } });
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("names each pattern by its label, adding where it runs only to tell alike patterns apart, never the route_id first", () => {
    mockRoutes([
      { route_id: "3968526772", route_code: "3968526772", route_short_name: "2-5", route_long_name: "2号線", trip_headsigns: [], origin_name: "県庁前", destination_name: "府中ニュータウン" },
      { route_id: "1757472590", route_code: "1757472590", route_short_name: "2-5", route_long_name: "2号線", trip_headsigns: [], origin_name: "県庁前", destination_name: "府中永田転回場" },
      { route_id: "2000000001", route_code: "2000000001", route_short_name: "2-1", route_long_name: "2号線", trip_headsigns: [] },
    ]);
    renderWithProviders(<PatternFilters agencyId={1} codes={["2000000001"]} onChange={vi.fn()} />);
    expect(screen.getByRole("option", { name: "2-1 2号線" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "2-5 2号線 · 県庁前 → 府中ニュータウン" })).toHaveAttribute("title", "3968526772");
    expect(screen.getByRole("option", { name: "2-5 2号線 · 県庁前 → 府中永田転回場" })).toBeInTheDocument();
  });
});
