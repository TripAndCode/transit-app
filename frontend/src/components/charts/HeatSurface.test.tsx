import type { ReactNode } from "react";
import { screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, it, expect, vi } from "vitest";
import { renderWithProviders } from "../../test/renderWithProviders";
import { stubReducedMotion } from "../../test/reducedMotion";
import { HeatSurface } from "./HeatSurface";
import { TrendFocusProvider } from "./TrendFocusContext";
import { useTrendFocus } from "./trendFocus";
import type { ForecastOverviewGridCell } from "../../api/types";

// The component's own function body counts its renders (see crossfilter.test.tsx
// for why a <Profiler> cannot stand in for this).
const { renders, countRenders } = vi.hoisted(() => {
  const renders = { surface: 0 };
  function countRenders<P>(render: (props: P) => ReactNode) {
    return (props: P) => {
      renders.surface += 1;
      return render(props);
    };
  }
  return { renders, countRenders };
});
vi.mock("./HeatSurface", async (importOriginal) => {
  const mod = await importOriginal<typeof import("./HeatSurface")>();
  return { ...mod, HeatSurface: countRenders(mod.HeatSurface) };
});

const hourly = [{ date: "2026-10-05", hour: 8, avg_min: 4, samples: 10 }, { date: "2026-10-11", hour: 20, avg_min: 1, samples: 10 }];
const grid: ForecastOverviewGridCell[] = [{ dow: 1, band: "morning", expected_avg_min: 2, samples: 10, low_confidence: false }];
const surface = () => <HeatSurface hourly={hourly} grid={grid} worst={null} rangeDays={14} />;
const mount = () => renderWithProviders(surface());
const cellAt = (dow: number, hour: number) => document.querySelector<HTMLElement>(`.heat-surface [data-dow="${dow}"][data-hour="${hour}"]`)!;

/** Publishes another linked chart's focus, as DailyChart would on hover. */
function OtherChart() {
  const { setFocus } = useTrendFocus();
  return <button type="button" onClick={() => setFocus({ source: "daily", date: "2026-10-06", dow: 2 })}>other</button>;
}

function providerSelectors(): string[] {
  const css = document.querySelector(".trend-focus > style")!.textContent ?? "";
  return css.split("}").map((r) => r.split("{")[0].trim()).filter(Boolean);
}

afterEach(() => vi.restoreAllMocks());

describe("HeatSurface", () => {
  it("renders a 7 × 24 grid in weekday rows with the generated dim rules", () => {
    mount();
    expect(screen.getAllByRole("gridcell")).toHaveLength(168);
    expect(screen.getAllByRole("row")).toHaveLength(7);
    expect(document.querySelector(".heat-surface-card style")!.textContent).toContain('.heat-surface[data-focus-hour="0"][data-focus-dow="1"]');
  });
  it("keeps exactly one cell in the tab order, so the grid is one tab stop", () => {
    mount();
    const tabbable = screen.getAllByRole("gridcell").filter((c) => c.getAttribute("tabindex") === "0");
    expect(tabbable).toEqual([cellAt(1, 0)]);
    expect(screen.getAllByRole("gridcell").every((c) => c.hasAttribute("tabindex"))).toBe(true);
  });
  it("paints the observed profile by default and rings a moderate cell", () => {
    mount();
    expect(cellAt(1, 8).style.getPropertyValue("--c")).toBe("var(--d3)");
    expect(cellAt(1, 8).style.getPropertyValue("--ring")).toBe("1px");
    expect(cellAt(1, 9).style.getPropertyValue("--c")).toBe("var(--none)");
    expect(cellAt(1, 9).style.getPropertyValue("--ring")).toBe("0px");
    expect(screen.getByRole("button", { name: "Observed" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("switching the profile recolours cells in place -- same nodes, new --c", async () => {
    mount();
    const before = cellAt(1, 8);
    await userEvent.click(screen.getByRole("button", { name: "Forecast" }));
    const after = cellAt(1, 8);
    expect(after).toBe(before);
    expect(after.style.getPropertyValue("--c")).toBe("var(--d1)");
    expect(after.style.getPropertyValue("--ring")).toBe("0px");
    expect(cellAt(7, 20).style.getPropertyValue("--c")).toBe("var(--none)");
    expect(screen.getByRole("button", { name: "Forecast" }).getAttribute("aria-pressed")).toBe("true");
  });
  it("under reduced motion the profile switch writes the same values (the tokens, not the code, go to 0)", async () => {
    stubReducedMotion();
    mount();
    await userEvent.click(screen.getByRole("button", { name: "Forecast" }));
    expect(cellAt(1, 8).style.getPropertyValue("--c")).toBe("var(--d1)");
  });
  it("hover writes the focus attributes on the grid and the readout text without re-rendering", () => {
    mount();
    const gridEl = screen.getByRole("grid");
    const before = renders.surface;
    fireEvent.mouseOver(cellAt(1, 8));
    expect(gridEl.getAttribute("data-focus-dow")).toBe("1");
    expect(gridEl.getAttribute("data-focus-hour")).toBe("8");
    expect(screen.getByTestId("heat-surface-readout").textContent).toContain("Mon 8:00");
    expect(screen.getByTestId("heat-surface-readout").textContent).toContain("4.0 min");
    fireEvent.mouseOver(cellAt(1, 9));
    expect(screen.getByTestId("heat-surface-readout").textContent).toContain("no data");
    fireEvent.mouseLeave(gridEl);
    expect(gridEl.hasAttribute("data-focus-dow")).toBe(false);
    expect(gridEl.hasAttribute("data-focus-hour")).toBe(false);
    expect(renders.surface - before).toBe(0);
  });
  it("arrow keys move focus across the grid, the tab stop follows, and focus publishes like hover", async () => {
    mount();
    cellAt(1, 8).focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(cellAt(1, 9));
    await userEvent.keyboard("{ArrowDown}");
    expect(document.activeElement).toBe(cellAt(2, 9));
    expect(screen.getByRole("grid").getAttribute("data-focus-dow")).toBe("2");
    expect(screen.getByRole("grid").getAttribute("data-focus-hour")).toBe("9");
    await userEvent.keyboard("{End}");
    expect(document.activeElement).toBe(cellAt(2, 23));
    await userEvent.keyboard("{ArrowRight}{ArrowUp}{ArrowUp}");
    expect(document.activeElement).toBe(cellAt(1, 23));
    await userEvent.keyboard("{Home}");
    expect(document.activeElement).toBe(cellAt(1, 0));
    expect(screen.getAllByRole("gridcell").filter((c) => c.getAttribute("tabindex") === "0")).toEqual([cellAt(1, 0)]);
    await userEvent.keyboard("{ArrowLeft}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}{ArrowDown}");
    expect(document.activeElement).toBe(cellAt(7, 0));
    expect(screen.getAllByRole("gridcell").filter((c) => c.getAttribute("tabindex") === "0")).toEqual([cellAt(7, 0)]);
  });
  it("a profile switch re-renders the cells but leaves the moved tab stop where focus put it", async () => {
    mount();
    cellAt(4, 12).focus();
    await userEvent.click(screen.getByRole("button", { name: "Forecast" }));
    expect(screen.getAllByRole("gridcell").filter((c) => c.getAttribute("tabindex") === "0")).toEqual([cellAt(4, 12)]);
  });
  it("says so when neither profile holds data", () => {
    renderWithProviders(<HeatSurface hourly={[]} grid={[]} worst={null} rangeDays={14} />);
    expect(screen.queryByRole("grid")).toBeNull();
    expect(screen.getByText("No data for this period.")).toBeTruthy();
  });
  it("names the worst weekday band over the range", () => {
    renderWithProviders(<HeatSurface hourly={hourly} grid={grid} worst={{ dow: 1, band: "morning", expected_avg_min: 2, samples: 10 }} rangeDays={14} />);
    expect(screen.getByText(/Over the past 14 days, Mon .* runs latest \(avg \+2\.0 min\)/)).toBeTruthy();
  });
});

describe("HeatSurface in the linked trend view", () => {
  it("publishes its weekday and hour to the provider without re-rendering itself", () => {
    renderWithProviders(<TrendFocusProvider>{surface()}</TrendFocusProvider>);
    const wrapper = document.querySelector<HTMLElement>(".trend-focus")!;
    const before = renders.surface;
    fireEvent.mouseOver(cellAt(3, 17));
    expect(wrapper.dataset.focusSource).toBe("dow");
    expect(wrapper.dataset.focusDow).toBe("3");
    expect(wrapper.dataset.focusHour).toBe("17");
    fireEvent.mouseOver(cellAt(3, 18));
    fireEvent.mouseLeave(screen.getByRole("grid"));
    expect(wrapper.dataset.focusSource).toBeUndefined();
    expect(wrapper.dataset.focusHour).toBeUndefined();
    expect(renders.surface - before).toBe(0);
  });
  it("recedes the weekdays another chart's focus disagrees with, and never dims from its own", async () => {
    renderWithProviders(
      <TrendFocusProvider>
        <OtherChart />
        {surface()}
      </TrendFocusProvider>,
    );
    expect(cellAt(1, 8).classList).toContain("focus-dim-opacity");
    await userEvent.click(screen.getByRole("button", { name: "other" }));
    const dimmed = (el: Element) => providerSelectors().some((sel) => el.matches(sel));
    expect(dimmed(cellAt(2, 8))).toBe(false);
    expect(dimmed(cellAt(1, 8))).toBe(true);
    expect(dimmed(cellAt(7, 23))).toBe(true);
    fireEvent.mouseOver(cellAt(1, 8));
    expect(dimmed(cellAt(2, 8))).toBe(false);
    expect(dimmed(cellAt(1, 9))).toBe(false);
  });
});
