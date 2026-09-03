import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { CityMapHero } from "./CityMapHero";

describe("CityMapHero", () => {
  it("renders a decorative canvas without crashing, even without 2D canvas support", () => {
    // jsdom has no `canvas` npm package installed, so
    // HTMLCanvasElement.prototype.getContext("2d") returns null here --
    // this is exactly the environment CityMapHero.tsx's own null-context
    // guard exists for.
    const { container } = render(<CityMapHero />);
    const canvas = container.querySelector("canvas");
    expect(canvas).not.toBeNull();
    expect(canvas).toHaveAttribute("aria-hidden", "true");
  });

  it("cleans up its resize listener and animation frame on unmount without throwing", () => {
    const { unmount } = render(<CityMapHero />);
    expect(() => unmount()).not.toThrow();
  });
});
