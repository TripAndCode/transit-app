import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { PreviewMapCanvas } from "./PreviewMapCanvas";

describe("PreviewMapCanvas", () => {
  it("renders a decorative canvas without crashing, even without 2D canvas support", () => {
    // jsdom has no `canvas` npm package installed, so
    // HTMLCanvasElement.prototype.getContext("2d") returns null here -- the
    // shared useCityMapAnimation hook's own null-context guard covers this.
    const { container } = render(<PreviewMapCanvas filterCss="none" />);
    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    expect(canvas).toHaveAttribute("aria-hidden", "true");
  });

  it("applies the caller-supplied CSS filter to the canvas element", () => {
    const { container } = render(<PreviewMapCanvas filterCss="grayscale(0.45)" />);
    const canvas = container.querySelector("canvas") as HTMLCanvasElement;
    expect(canvas.style.filter).toBe("grayscale(0.45)");
  });

  it("cleans up its resize listener and animation frame on unmount without throwing", () => {
    const { unmount } = render(<PreviewMapCanvas filterCss="none" />);
    expect(() => unmount()).not.toThrow();
  });
});
