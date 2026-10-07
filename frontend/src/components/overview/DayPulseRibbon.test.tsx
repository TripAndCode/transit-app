import { render } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import { DayPulseRibbon } from "./DayPulseRibbon";

describe("DayPulseRibbon", () => {
  it("is decorative: aria-hidden, no pointer events, 16% area fill", () => {
    const { container } = render(<DayPulseRibbon byHour={Array.from({ length: 24 }, (_, h) => h / 4)} />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg.classList.contains("ov-pulse-ribbon")).toBe(true);
    expect(svg.querySelector("path.ov-pulse-ribbon__area")).toHaveAttribute("opacity", "0.16");
    expect(svg.querySelector("linearGradient stop")).toHaveAttribute("stop-color", "var(--d0)");
    // One horizontal axis for both paths: a bounding-box gradient would
    // stretch over a line that starts late and vanish on a flat one.
    const gradient = svg.querySelector("linearGradient")!;
    expect(gradient).toHaveAttribute("gradientUnits", "userSpaceOnUse");
    expect(gradient).toHaveAttribute("x2", "960");
  });
  it("renders nothing when every hour is null", () => {
    const { container } = render(<DayPulseRibbon byHour={Array.from({ length: 24 }, () => null)} />);
    expect(container.firstChild).toBeNull();
  });
});
