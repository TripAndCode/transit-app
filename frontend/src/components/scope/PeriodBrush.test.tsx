import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/renderWithProviders";
import { PeriodBrush } from "./PeriodBrush";

const DAYS = Array.from({ length: 28 }, (_, i) => ({
  date: `2026-09-${String(i + 1).padStart(2, "0")}`,
  avg_min: i === 25 ? 5.2 : 1.0,
  samples: 100,
})).filter((d) => d.date !== "2026-09-11");

function mount(from = "2026-09-20", to = "2026-09-28") {
  const onCommit = vi.fn();
  renderWithProviders(<PeriodBrush days={DAYS} latest="2026-09-28" from={from} to={to} onCommit={onCommit} />);
  return onCommit;
}

describe("PeriodBrush", () => {
  afterEach(() => vi.restoreAllMocks());

  it("hatches a day with no data instead of drawing it as zero", () => {
    const { container } = renderWithProviders(
      <PeriodBrush days={DAYS} latest="2026-09-28" from="2026-09-20" to="2026-09-28" onCommit={vi.fn()} />,
    );
    expect(container.querySelectorAll(".scope-brush__gap")).toHaveLength(1);
    expect(container.querySelectorAll(".scope-brush__bar")).toHaveLength(27);
  });

  it("colours each day by the delay ramp", () => {
    const { container } = renderWithProviders(
      <PeriodBrush days={DAYS} latest="2026-09-28" from="2026-09-20" to="2026-09-28" onCommit={vi.fn()} />,
    );
    const bars = container.querySelectorAll(".scope-brush__bar");
    expect(bars[0].getAttribute("fill")).toBe("var(--d0)");
    expect(bars[24].getAttribute("fill")).toBe("var(--d4)");
  });

  it("selects the days a drag crosses, whichever way it goes", () => {
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockReturnValue({
      left: 0, top: 0, width: 280, height: 48, right: 280, bottom: 48, x: 0, y: 0, toJSON: () => ({}),
    } as DOMRect);
    const onCommit = mount();
    const strip = screen.getByRole("img", { name: /daily mean delay/i });
    fireEvent.pointerDown(strip, { clientX: 205, pointerId: 1 });
    fireEvent.pointerMove(strip, { clientX: 155, pointerId: 1 });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.pointerUp(strip, { clientX: 155, pointerId: 1 });
    expect(onCommit).toHaveBeenCalledWith("2026-09-16", "2026-09-21");
  });

  it("moves a handle a day at a time from the keyboard, writing on key up", () => {
    const onCommit = mount();
    const start = screen.getByRole("slider", { name: "Start" });
    expect(start).toHaveAttribute("aria-valuetext", "9/20");
    fireEvent.keyDown(start, { key: "ArrowRight" });
    expect(onCommit).not.toHaveBeenCalled();
    fireEvent.keyUp(start, { key: "ArrowRight" });
    expect(onCommit).toHaveBeenCalledWith("2026-09-21", "2026-09-28");
  });

  it("never moves the start past the end", () => {
    const onCommit = mount("2026-09-28", "2026-09-28");
    const start = screen.getByRole("slider", { name: "Start" });
    fireEvent.keyDown(start, { key: "ArrowRight" });
    fireEvent.keyUp(start, { key: "ArrowRight" });
    expect(onCommit).not.toHaveBeenCalled();
  });
});
