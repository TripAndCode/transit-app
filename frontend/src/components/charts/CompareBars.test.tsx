import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { stubReducedMotion } from "../../test/reducedMotion";
import * as useRouteNamesModule from "../../api/useRouteNames";
import * as useAgencyIdModule from "../../api/useAgencyId";
import { CompareBars } from "./CompareBars";

const ROWS = [["3", 3.4, 2.7, 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7], ["2", 2.2, 3.5, 1.3, 1.3]];
beforeEach(() => {
  stubReducedMotion();
  vi.spyOn(useAgencyIdModule, "useAgencyId").mockReturnValue(1);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({ data: new Map(), isLoading: false, format: (c) => c ?? "—" });
});
afterEach(() => vi.restoreAllMocks());
const mount = (search = "", rows: unknown[][] = ROWS) =>
  renderWithProviders(<MemoryRouter initialEntries={[`/agencies/1/compare${search}`]}><CompareBars rows={rows} resetKey="scope-a" /></MemoryRouter>);

describe("CompareBars", () => {
  it("shows weekday first, worst on top, each row keyed for FLIP with a scaled bar and a ghost of the other period", () => {
    mount();
    const rows = screen.getAllByTestId("compare-bar-row");
    expect(rows.map((r) => r.getAttribute("data-flip-key"))).toEqual(["12", "3", "2"]);
    const fill = rows[0].querySelector<HTMLElement>(".compare-bar__fill")!;
    const ghost = rows[0].querySelector<HTMLElement>(".compare-bar__ghost")!;
    expect(fill.style.getPropertyValue("--bar-share")).toBe(String(3.8 / 6));
    expect(ghost.style.getPropertyValue("--bar-share")).toBe(String(3.1 / 6));
    expect(fill.style.width).toBe("");
  });
  it("switching the period re-sorts, swaps fill and ghost, and prints the signed delta against the other period", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday" }));
    const rows = screen.getAllByTestId("compare-bar-row");
    expect(rows.map((r) => r.getAttribute("data-flip-key"))).toEqual(["2", "12", "3"]);
    expect(rows[0].querySelector(".compare-bar__delta")!.textContent).toBe("+1.3");
    expect(rows[0].querySelector(".compare-bar__delta")!.classList.contains("compare-bar__delta--up")).toBe(true);
    expect(rows[1].querySelector(".compare-bar__delta")!.textContent).toBe("-0.7");
  });
  it("a gap that rounds to nothing prints as an unsigned, neutral 0.0 in either period", async () => {
    mount("", [["7", 1.24, 1.2, 0.04, -0.04]]);
    const delta = () => screen.getByTestId("compare-bar-row").querySelector(".compare-bar__delta")!;
    expect(delta().textContent).toBe("0.0");
    expect(delta().className).toBe("compare-bar__delta num");
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday" }));
    expect(delta().textContent).toBe("0.0");
    expect(delta().className).toBe("compare-bar__delta num");
  });
  it("a raised cap holds across a period switch, which re-ranks the same routes, and resets for a new list", async () => {
    const many = Array.from({ length: 250 }, (_, i) => [`r${i}`, 1 + i / 100, 2 - i / 100, 0, 0]);
    const ui = (resetKey: string) => (
      <MemoryRouter initialEntries={["/agencies/1/compare"]}>
        <CompareBars rows={many} resetKey={resetKey} />
      </MemoryRouter>
    );
    const { rerender } = renderWithProviders(ui("scope-a"));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(200);
    await userEvent.click(screen.getByRole("button", { name: /Show 50 more/ }));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(250);
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday" }));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(250);
    rerender(ui("scope-b"));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(200);
  });
  it("reads the period from the URL and the value carries the numeric face", () => {
    mount("?period=weekend");
    expect(screen.getByRole("button", { name: "Weekend/Holiday" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByTestId("compare-bar-row")[0].querySelector(".compare-bar__value")!.classList.contains("num")).toBe(true);
  });
});
