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
    // Two variants that read alike are told apart by their route_ids, the last resort.
    const variant = screen.getByText("C12 Loop (C12(1))").closest("label")!;
    expect(px(variant.style.minHeight)).toBeGreaterThanOrEqual(32);
  });
});

describe("RoutesPicker labels", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a line by its plate and name, and each variant by where it runs, never by its route_id alone", async () => {
    vi.spyOn(hooks, "useRoutes").mockReturnValue({
      data: [
        { route_id: "3968526772", route_short_name: "2-5", route_long_name: "2号線", route_code: "3968526772", trip_headsigns: [], origin_name: "県庁前", destination_name: "府中ニュータウン" },
        { route_id: "1757472590", route_short_name: "2-5", route_long_name: "2号線", route_code: "1757472590", trip_headsigns: [], origin_name: "県庁前", destination_name: "府中永田転回場" },
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
    expect(screen.getByRole("checkbox", { name: "2-5 2号線" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Expand" }));
    expect(screen.getByText("県庁前 → 府中ニュータウン")).toHaveAttribute("title", "3968526772");
    expect(screen.getByText("県庁前 → 府中永田転回場")).toBeInTheDocument();
    expect(screen.queryByText("3968526772")).toBeNull();
  });
});

describe("RoutesPicker brand lines", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names a line whose variants share a brand but not a line name by the brand, never a route_id", () => {
    vi.spyOn(hooks, "useRoutes").mockReturnValue({
      data: [
        { route_id: "j1", route_short_name: "めいぷる～ぷ", route_long_name: "めいぷる～ぷ（新天地経由）", route_code: "j1", trip_headsigns: [] },
        { route_id: "j2", route_short_name: "めいぷる～ぷ", route_long_name: "めいぷる～ぷ（運動公園経由）", route_code: "j2", trip_headsigns: [] },
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
    const checkbox = screen.getByRole("checkbox", { name: "めいぷる～ぷ" });
    expect(checkbox.parentElement).toHaveTextContent(/^めいぷる～ぷ/);
    expect(checkbox.parentElement).not.toHaveTextContent("j1");
  });
});

describe("RoutesPicker group checkbox", () => {
  afterEach(() => vi.restoreAllMocks());

  it("is named after its line, so a screen reader does not announce a bare checkbox", () => {
    renderPicker();
    const checkbox = screen.getByRole("checkbox");
    const expand = screen.getByRole("button", { name: "Expand" });
    const lineName = expand.parentElement!.querySelector("span")!.textContent!;
    expect(lineName).not.toBe("");
    expect(screen.getByRole("checkbox", { name: lineName })).toBe(checkbox);
  });

  it("finds a line typed with full-width characters", async () => {
    renderPicker();
    await userEvent.type(screen.getByLabelText("Search routes"), "Ｃ１２");
    expect(screen.getByRole("button", { name: "Expand" })).toBeInTheDocument();
  });
});
