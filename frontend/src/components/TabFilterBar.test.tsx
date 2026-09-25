import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { TabFilterBar } from "./TabFilterBar";
import * as hooks from "../api/hooks";
import type { Route as ApiRoute } from "../api/types";

// Mirrors defaultRangeAnchor.test.tsx's Probe pattern: shares the router
// context with TabFilterBar so it reactively sees whatever setCtx() writes.
function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function renderFilterBar(initialPath: string, routes: ApiRoute[] = []) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: routes, isPending: false } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          path="/agencies/:agencyId/overview"
          element={
            <>
              <TabFilterBar />
              <Probe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("TabFilterBar reset", () => {
  afterEach(() => vi.restoreAllMocks());

  it("clears from/to instead of hardcoding today's window, so useDefaultRangeAnchor can re-anchor a lagging agency", () => {
    // Simulates a lagging agency: the URL carries the smart-default-range
    // anchor's own from/to plus a user-applied dow filter.
    renderFilterBar("/agencies/1/overview?from=2026-05-12&to=2026-06-10&dow=weekday");
    fireEvent.click(screen.getByText("Clear all"));
    const params = new URLSearchParams(screen.getByTestId("params").textContent ?? "");
    expect(params.get("from")).toBeNull();
    expect(params.get("to")).toBeNull();
  });
});

describe("TabFilterBar route chip labels", () => {
  afterEach(() => vi.restoreAllMocks());

  it("falls back to route_long_name when route_short_name is blank, mirroring useRouteNames", () => {
    // Same fallback order bug as useRouteNames.ts: a route with a blank
    // route_short_name should show route_long_name here too, not the raw
    // route_id (which redundantly re-embeds the parenthesised code).
    renderFilterBar("/agencies/1/overview?routes=1021", [
      { route_id: "国道・古川線(1021)", route_short_name: "", route_long_name: "国道・古川線", route_code: "1021", trip_headsigns: [] },
    ]);
    expect(screen.getByText("国道・古川線 (1021)")).toBeInTheDocument();
  });
});

describe("TabFilterBar popover dismissal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("closes on Escape and puts focus back on the filter button", async () => {
    // The popover is opened from the keyboard as often as from the mouse,
    // and it has no close control of its own: without Escape the only way
    // out is a click somewhere else on the page.
    const user = userEvent.setup();
    renderFilterBar("/agencies/1/overview");
    const trigger = screen.getByRole("button", { name: /Filters/ });

    await user.click(trigger);
    screen.getByRole("button", { name: "All days" }).focus();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("button", { name: "All days" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
