import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import i18n from "../../i18n";
import { stubReducedMotion } from "../../test/reducedMotion";
import * as useRouteNamesModule from "../../api/useRouteNames";
import * as useAgencyIdModule from "../../api/useAgencyId";
import { CompareBars } from "./CompareBars";

// Printed gaps (weekday minus weekend): "3" +0.7, "12" +0.7, "2" -1.3.
const ROWS = [["3", 3.4, 2.7, 0.7, 0.7], ["12", 3.8, 3.1, 0.7, 0.7], ["2", 2.2, 3.5, 1.3, 1.3]];
beforeEach(() => {
  stubReducedMotion();
  vi.spyOn(useAgencyIdModule, "useAgencyId").mockReturnValue(1);
  vi.spyOn(useRouteNamesModule, "useRouteNames").mockReturnValue({ ...useRouteNamesModule.buildRouteNames([], i18n.t), format: (c) => c ?? "—" });
});
afterEach(() => vi.restoreAllMocks());
const mount = (search = "", rows: unknown[][] = ROWS) =>
  renderWithProviders(<MemoryRouter initialEntries={[`/agencies/1/compare${search}`]}><CompareBars rows={rows} resetKey="scope-a" /></MemoryRouter>);
const keys = () => screen.getAllByTestId("compare-bar-row").map((r) => r.getAttribute("data-flip-key"));
const share = (row: HTMLElement, part: "fill" | "ghost") =>
  row.querySelector<HTMLElement>(`.compare-bar__${part}`)!.style.getPropertyValue("--bar-share");

describe("CompareBars", () => {
  it("ranks by the size of the gap by default, under headings that name each side's unit and the gap", () => {
    mount();
    expect(keys()).toEqual(["2", "12", "3"]);
    const head = screen.getByTestId("compare-bars-head");
    expect(head).toHaveTextContent("Route");
    expect(head).toHaveTextContent("Weekday (min)");
    expect(head).toHaveTextContent("Weekend/Holiday (min)");
    expect(head).toHaveTextContent("Gap");
    expect(screen.getByRole("button", { name: "Largest gap" })).toHaveAttribute("aria-pressed", "true");
  });

  it("prints both sides' figures and a gap sentence naming the later side, the same in every order", async () => {
    mount();
    const first = () => screen.getAllByTestId("compare-bar-row").find((r) => r.getAttribute("data-flip-key") === "2")!;
    expect(within(first()).getByTestId("compare-bar-weekday")).toHaveTextContent("2.2");
    expect(within(first()).getByTestId("compare-bar-weekend")).toHaveTextContent("3.5");
    expect(within(first()).getByTestId("compare-bar-gap")).toHaveTextContent("Weekend/Holiday 1.3 min later");
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday: latest first" }));
    expect(within(first()).getByTestId("compare-bar-gap")).toHaveTextContent("Weekend/Holiday 1.3 min later");
    const other = screen.getAllByTestId("compare-bar-row").find((r) => r.getAttribute("data-flip-key") === "12")!;
    expect(within(other).getByTestId("compare-bar-gap")).toHaveTextContent("Weekday 0.7 min later");
  });

  it("states a gap that prints as 0.0 as no gap, in neutral text with no up or down tone", () => {
    mount("", [["7", 1.24, 1.2, 0.04, -0.04]]);
    const gap = screen.getByTestId("compare-bar-gap");
    expect(gap).toHaveTextContent("No gap");
    expect(gap.className).toBe("compare-bar__gap");
  });

  it("ranking by one side re-sorts by its level and fills that side, with the other as the dashed ghost", async () => {
    mount();
    let row = screen.getAllByTestId("compare-bar-row")[0];
    expect(share(row, "fill")).toBe(String(2.2 / 6));
    expect(share(row, "ghost")).toBe(String(3.5 / 6));
    await userEvent.click(screen.getByRole("button", { name: "Weekday: latest first" }));
    expect(keys()).toEqual(["12", "3", "2"]);
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday: latest first" }));
    expect(keys()).toEqual(["2", "12", "3"]);
    row = screen.getAllByTestId("compare-bar-row")[0];
    expect(share(row, "fill")).toBe(String(3.5 / 6));
    expect(share(row, "ghost")).toBe(String(2.2 / 6));
    expect(row.querySelector(".compare-bar__track")!.lastElementChild).toBe(row.querySelector(".compare-bar__ghost"));
  });

  it("draws every bar against one axis stepped from the largest figure on either side, which no order changes", async () => {
    // The largest figure is a weekend 9.0, so the axis is 12 minutes whichever side is shown.
    mount("", [["a", 4.5, 9, 4.5, 4.5], ["b", 6, 3, 3, 3]]);
    expect(screen.getByTestId("compare-bars-legend")).toHaveTextContent("Axis 0–12 min");
    const byKey = (k: string) => screen.getAllByTestId("compare-bar-row").find((r) => r.getAttribute("data-flip-key") === k)!;
    expect(share(byKey("b"), "fill")).toBe(String(6 / 12));
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday: latest first" }));
    expect(screen.getByTestId("compare-bars-legend")).toHaveTextContent("Axis 0–12 min");
    expect(share(byKey("a"), "fill")).toBe(String(9 / 12));
  });

  it("fills bars with the shared delay steps, never the old severe ramp", () => {
    mount("", [["a", 6.5, 1, 5.5, -5.5]]);
    const fill = screen.getByTestId("compare-bar-row").querySelector<HTMLElement>(".compare-bar__fill")!;
    expect(fill.style.background).toBe("var(--d4)");
  });

  it("a raised cap holds across an order switch, which re-ranks the same routes, and resets for a new list", async () => {
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
    await userEvent.click(screen.getByRole("button", { name: "Weekend/Holiday: latest first" }));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(250);
    rerender(ui("scope-b"));
    expect(screen.getAllByTestId("compare-bar-row")).toHaveLength(200);
  });

  it("reads the order from the URL and the figures carry the numeric face", () => {
    mount("?rank=weekend");
    expect(screen.getByRole("button", { name: "Weekend/Holiday: latest first" })).toHaveAttribute("aria-pressed", "true");
    expect(keys()).toEqual(["2", "12", "3"]);
    expect(screen.getAllByTestId("compare-bar-weekday")[0].classList.contains("num")).toBe(true);
  });
});
