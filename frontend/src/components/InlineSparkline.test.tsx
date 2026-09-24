import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { InlineSparkline } from "./InlineSparkline";

describe("InlineSparkline", () => {
  it("does not set preserveAspectRatio by default", () => {
    const { container } = render(<InlineSparkline points={[1, 2, 3]} />);
    expect(container.querySelector("svg")?.getAttribute("preserveAspectRatio")).toBeNull();
  });

  it("passes preserveAspectRatio through, for a full-bleed background use over a fixed-ratio box", () => {
    const { container } = render(<InlineSparkline points={[1, 2, 3]} preserveAspectRatio="none" />);
    expect(container.querySelector("svg")?.getAttribute("preserveAspectRatio")).toBe("none");
  });
});
