import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { stubReducedMotion } from "../../test/reducedMotion";
import * as useRouteNamesModule from "../../api/useRouteNames";
import * as useAgencyIdModule from "../../api/useAgencyId";
import { CompareBars } from "./CompareBars";

const ROWS = [["3", 3.4, 2.7, 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7], ["2", 2.2, 2.6, 0.4, -0.4]];
beforeEach(() => {
  stubReducedMotion();
  vi.spyOn(useAgencyIdModule, "useAgencyId").mockReturnValue(1);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({ data: new Map(), isLoading: false, format: (c) => c ?? "—" });
});
afterEach(() => vi.restoreAllMocks());
const mount = (search = "") => renderWithProviders(<MemoryRouter initialEntries={[`/agencies/1/compare${search}`]}><CompareBars rows={ROWS} /></MemoryRouter>);

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
    expect(rows.map((r) => r.getAttribute("data-flip-key"))).toEqual(["12", "3", "2"]);
    expect(rows[2].querySelector(".compare-bar__delta")!.textContent).toBe("+0.4");
    expect(rows[2].querySelector(".compare-bar__delta")!.classList.contains("compare-bar__delta--up")).toBe(true);
    expect(rows[0].querySelector(".compare-bar__delta")!.textContent).toBe("-0.7");
  });
  it("reads the period from the URL and the value carries the numeric face", () => {
    mount("?period=weekend");
    expect(screen.getByRole("button", { name: "Weekend/Holiday" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByTestId("compare-bar-row")[0].querySelector(".compare-bar__value")!.classList.contains("num")).toBe(true);
  });
});
