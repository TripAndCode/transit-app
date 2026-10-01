import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PeriodBrush } from "./PeriodBrush";

// 9/2..9/28 with 9/11 missing; 9/26 is a heavy day. 8/31 and 9/1 are in the
// window (earliest data 8/31) but have no data.
const DAYS = Array.from({ length: 27 }, (_, i) => ({
  date: `2026-09-${String(i + 2).padStart(2, "0")}`,
  avg_min: i + 2 === 26 ? 5.2 : 1.0,
  samples: 100,
})).filter((d) => d.date !== "2026-09-11");

const WIDTH = 290; // 29 calendar days (8/31..9/28) at 10px each

function mount({ from = "2026-09-20", to = "2026-09-28", windowFrom = "2026-08-31" } = {}) {
  const onCommit = vi.fn();
  const view = renderWithProviders(
    <PeriodBrush days={DAYS} windowFrom={windowFrom} latest="2026-09-28" from={from} to={to} onCommit={onCommit} />,
  );
  return { onCommit, container: view.container };
}

/** The x offset of a calendar day's middle. */
const xOf = (dayIndex: number) => dayIndex * 10 + 5;
const strip = () => screen.getByRole("img", { name: "Daily mean delay" });

describe("PeriodBrush", () => {
  beforeEach(() => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, width: WIDTH, height: 48, right: WIDTH, bottom: 48, x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect);
  });
  afterEach(() => vi.restoreAllMocks());

  describe("drawing", () => {
    it("marks days without data, leading ones included, as short no-data marks", () => {
      const { container } = mount();
      const gaps = container.querySelectorAll(".scope-brush__gap");
      expect(gaps).toHaveLength(3);
      expect(gaps[0].querySelector("title")?.textContent).toBe("No data");
      expect(Number(gaps[0].getAttribute("height"))).toBeLessThan(10);
      expect(container.querySelectorAll(".scope-brush__bar")).toHaveLength(26);
    });

    it("colours each day by the delay ramp and names its date and mean", () => {
      const { container } = mount();
      const bars = container.querySelectorAll(".scope-brush__bar");
      expect(bars[0].getAttribute("fill")).toBe("var(--d0)");
      expect(bars[0].querySelector("title")?.textContent).toBe("9/2 · 1.0 min");
      expect([...bars].find((b) => b.querySelector("title")?.textContent?.startsWith("9/26"))?.getAttribute("fill")).toBe(
        "var(--d4)",
      );
    });

    it("outlines the period and dims the days outside it, so calm days stay visible", () => {
      const { container } = mount();
      const selection = container.querySelector(".scope-brush__selection");
      expect(selection?.getAttribute("fill")).toBe("none");
      const out = container.querySelectorAll(".scope-brush__bar--out");
      expect(out.length).toBeGreaterThan(0);
      expect(out.length).toBeLessThan(26);
    });

    it("draws no selection for a period wholly outside the window", () => {
      const { container } = mount({ from: "2026-07-01", to: "2026-07-10" });
      expect(container.querySelector(".scope-brush__selection")).toBeNull();
    });

    it("explains the colours in a legend", () => {
      mount();
      expect(screen.getByText("under 1.5 min")).toBeInTheDocument();
      expect(screen.getByText("5 min or more")).toBeInTheDocument();
      expect(screen.getByText("No data", { selector: ".scope-legend span" })).toBeInTheDocument();
    });
  });

  describe("pointer", () => {
    it("selects the days a drag crosses, whichever way it goes", () => {
      const { onCommit } = mount();
      fireEvent.pointerDown(strip(), { clientX: xOf(21), pointerId: 1, button: 0 });
      fireEvent.pointerMove(strip(), { clientX: xOf(16), pointerId: 1 });
      expect(onCommit).not.toHaveBeenCalled();
      fireEvent.pointerUp(strip(), { clientX: xOf(16), pointerId: 1 });
      expect(onCommit).toHaveBeenCalledWith("2026-09-16", "2026-09-21");
    });

    it("ignores a tap that never crosses a day", () => {
      const { onCommit } = mount();
      fireEvent.pointerDown(strip(), { clientX: xOf(11), pointerId: 1, button: 0 });
      fireEvent.pointerUp(strip(), { clientX: xOf(11), pointerId: 1 });
      expect(onCommit).not.toHaveBeenCalled();
    });

    it("drops a drag the browser cancels", () => {
      const { onCommit } = mount();
      fireEvent.pointerDown(strip(), { clientX: xOf(5), pointerId: 1, button: 0 });
      fireEvent.pointerMove(strip(), { clientX: xOf(9), pointerId: 1 });
      fireEvent.pointerCancel(strip(), { pointerId: 1 });
      fireEvent.pointerMove(strip(), { clientX: xOf(20), pointerId: 1 });
      fireEvent.pointerUp(strip(), { clientX: xOf(20), pointerId: 1 });
      expect(onCommit).not.toHaveBeenCalled();
    });

    it("ignores any button but the primary one", () => {
      const { onCommit } = mount();
      fireEvent.pointerDown(strip(), { clientX: xOf(5), pointerId: 1, button: 2 });
      fireEvent.pointerMove(strip(), { clientX: xOf(9), pointerId: 1 });
      fireEvent.pointerUp(strip(), { clientX: xOf(9), pointerId: 1 });
      expect(onCommit).not.toHaveBeenCalled();
    });

    it("moves one edge when a handle is dragged, keeping the other", () => {
      const { onCommit } = mount();
      const end = screen.getByRole("slider", { name: "Period end" });
      fireEvent.pointerDown(end, { clientX: xOf(28), pointerId: 1, button: 0 });
      fireEvent.pointerMove(end, { clientX: xOf(25), pointerId: 1 });
      fireEvent.pointerUp(end, { clientX: xOf(25), pointerId: 1 });
      expect(onCommit).toHaveBeenCalledWith("2026-09-20", "2026-09-25");
    });
  });

  it("stops a dragged handle at the other one rather than swapping them", () => {
    const { onCommit } = mount();
    const end = screen.getByRole("slider", { name: "Period end" });
    fireEvent.pointerDown(end, { clientX: xOf(28), pointerId: 1, button: 0 });
    fireEvent.pointerMove(end, { clientX: xOf(10), pointerId: 1 });
    fireEvent.pointerUp(end, { clientX: xOf(10), pointerId: 1 });
    expect(onCommit).toHaveBeenCalledWith("2026-09-20", "2026-09-20");
  });

  describe("keyboard", () => {
    it("moves a handle a day at a time, writing only that edge on key up", () => {
      const { onCommit } = mount();
      const start = screen.getByRole("slider", { name: "Period start" });
      expect(start).toHaveAttribute("aria-valuetext", "Sep 20");
      fireEvent.keyDown(start, { key: "ArrowRight" });
      expect(onCommit).not.toHaveBeenCalled();
      fireEvent.keyUp(start, { key: "ArrowRight" });
      expect(onCommit).toHaveBeenCalledWith("2026-09-21", "2026-09-28");
    });

    it("keeps an edge outside the window when only the other one moves", () => {
      const { onCommit } = mount({ from: "2026-06-01", to: "2026-09-28" });
      expect(screen.getByRole("slider", { name: "Period start" })).toHaveAttribute("aria-valuetext", "Jun 1");
      const end = screen.getByRole("slider", { name: "Period end" });
      fireEvent.keyDown(end, { key: "ArrowLeft" });
      fireEvent.keyUp(end, { key: "ArrowLeft" });
      expect(onCommit).toHaveBeenCalledWith("2026-06-01", "2026-09-27");
    });

    it("never moves the start past the end", () => {
      const { onCommit } = mount({ from: "2026-09-28", to: "2026-09-28" });
      const start = screen.getByRole("slider", { name: "Period start" });
      fireEvent.keyDown(start, { key: "ArrowRight" });
      fireEvent.keyUp(start, { key: "ArrowRight" });
      expect(onCommit).not.toHaveBeenCalled();
    });

    it("jumps to the window's ends with Home and End", () => {
      const { onCommit } = mount();
      const start = screen.getByRole("slider", { name: "Period start" });
      fireEvent.keyDown(start, { key: "Home" });
      fireEvent.keyUp(start, { key: "Home" });
      expect(onCommit).toHaveBeenCalledWith("2026-08-31", "2026-09-28");
    });

    it("writes a pending move when focus leaves the handle", () => {
      const { onCommit } = mount();
      const start = screen.getByRole("slider", { name: "Period start" });
      fireEvent.keyDown(start, { key: "ArrowLeft" });
      fireEvent.blur(start);
      expect(onCommit).toHaveBeenCalledWith("2026-09-19", "2026-09-28");
    });
  });
});
