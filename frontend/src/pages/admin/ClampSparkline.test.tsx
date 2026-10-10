import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ClampSparkline } from "./ClampSparkline";

const day = (n: number, clamp_pct: number | null) => ({ date: `2026-10-0${n}`, clamp_pct });

describe("ClampSparkline", () => {
  it("draws a marker for a lone observed day, since a one-point polyline renders nothing", () => {
    const { container } = render(<ClampSparkline days={[day(1, 1.2)]} label="clamp" />);
    expect(container.querySelectorAll("circle")).toHaveLength(1);
    expect(container.querySelectorAll("polyline")).toHaveLength(0);
  });

  it("marks each isolated observed day between gaps and lines the multi-day runs", () => {
    const days = [day(1, 1), day(2, null), day(3, 2), day(4, 3), day(5, null), day(6, 1)];
    const { container } = render(<ClampSparkline days={days} label="clamp" />);
    expect(container.querySelectorAll("circle")).toHaveLength(2);
    expect(container.querySelectorAll("polyline")).toHaveLength(1);
  });

  it("renders only a dash when nothing was observed", () => {
    const { container } = render(<ClampSparkline days={[day(1, null)]} label="clamp" />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
