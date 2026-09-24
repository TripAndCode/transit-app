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

  it("carries its position in the tab's one staggered entrance group as --stagger", () => {
    const { container } = render(
      <RevealSection index={2}>
        <p>content</p>
      </RevealSection>,
    );
    expect((container.firstElementChild as HTMLElement).style.getPropertyValue("--stagger")).toBe("2");
  });

  it("caps the stagger index so a long tab still finishes entering", () => {
    const { container } = render(
      <RevealSection index={9}>
        <p>content</p>
      </RevealSection>,
    );
    expect((container.firstElementChild as HTMLElement).style.getPropertyValue("--stagger")).toBe("4");
  });

  it("defaults to the head of the group when no index is given", () => {
    const { container } = render(
      <RevealSection>
        <p>content</p>
      </RevealSection>,
    );
    expect((container.firstElementChild as HTMLElement).style.getPropertyValue("--stagger")).toBe("0");
  });
});
