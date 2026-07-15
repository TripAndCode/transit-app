import { describe, it, expect, vi, afterEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useSearchParams } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import { TabFilterBar } from "./TabFilterBar";
import * as hooks from "../api/hooks";

// Mirrors defaultRangeAnchor.test.tsx's Probe pattern: shares the router
// context with TabFilterBar so it reactively sees whatever setCtx() writes.
function Probe() {
  const [params] = useSearchParams();
  return <div data-testid="params">{params.toString()}</div>;
}

function renderFilterBar(initialPath: string) {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({ data: [], isPending: false } as never);
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
