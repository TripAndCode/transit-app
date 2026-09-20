import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { RevealSection } from "./RevealSection";

describe("RevealSection", () => {
  it("renders its children immediately and visibly, not gated behind JS/observer timing", () => {
    render(
      <RevealSection>
        <p>always here</p>
      </RevealSection>,
    );
    expect(screen.getByText("always here")).toBeInTheDocument();
  });

  it("uses the transform-only .reveal class, never an inline opacity", () => {
    const { container } = render(
      <RevealSection>
        <p>content</p>
      </RevealSection>,
    );
    const wrapper = container.firstElementChild;
    expect(wrapper?.className.split(/\s+/)).toContain("reveal");
    expect(wrapper?.getAttribute("style") ?? "").not.toMatch(/opacity/);
  });
});
