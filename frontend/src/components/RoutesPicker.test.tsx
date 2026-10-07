import { describe, it, expect, vi, afterEach } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { renderWithProviders } from "../test/renderWithProviders";
import * as hooks from "../api/hooks";
import { RoutesPicker } from "./RoutesPicker";

function renderPicker() {
  vi.spyOn(hooks, "useRoutes").mockReturnValue({
    data: [
      { route_id: "C12(1)", route_short_name: "C12", route_long_name: "Loop", route_code: "C12a", trip_headsigns: [] },
      { route_id: "C12(2)", route_short_name: "C12", route_long_name: "Loop", route_code: "C12b", trip_headsigns: [] },
    ],
    isPending: false,
    refetch: vi.fn(),
  } as never);
  renderWithProviders(
    <MemoryRouter initialEntries={["/agencies/1/routes"]}>
      <Routes>
        <Route path="/agencies/:agencyId/routes" element={<RoutesPicker selected={[]} onChange={vi.fn()} />} />
      </Routes>
    </MemoryRouter>,
  );
}

const px = (value: string) => Number.parseFloat(value);

describe("RoutesPicker touch targets", () => {
  afterEach(() => vi.restoreAllMocks());

  it("gives a line's row and its expand button room for a finger", () => {
    renderPicker();
    const expand = screen.getByRole("button", { name: "Expand" });
    expect(px(expand.style.minWidth)).toBeGreaterThanOrEqual(32);
    expect(px(expand.style.minHeight)).toBeGreaterThanOrEqual(32);
    expect(px(expand.parentElement!.style.minHeight)).toBeGreaterThanOrEqual(36);
  });

  it("gives each variant row room for a finger", async () => {
    renderPicker();
    await userEvent.click(screen.getByRole("button", { name: "Expand" }));
    const variant = screen.getByText("C12a").closest("label")!;
    expect(px(variant.style.minHeight)).toBeGreaterThanOrEqual(32);
  });
});
