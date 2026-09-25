import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { RevealSection } from "./RevealSection";

type ObserverCallback = (entries: { isIntersecting: boolean }[]) => void;

/** A controllable IntersectionObserver stub -- unlike the inert no-op the
 *  global test setup installs (which never fires), this one hands back its
 *  captured callback so a test can drive an intersection itself. */
function driveIntersectionObserver(): { fire: (isIntersecting: boolean) => void } {
  let callback: ObserverCallback = () => {};
  class StubObserver {
    constructor(cb: ObserverCallback) {
      callback = cb;
    }
    observe = vi.fn();
    disconnect = vi.fn();
    unobserve = vi.fn();
    takeRecords = () => [];
    root = null;
    rootMargin = "";
    thresholds: number[] = [];
  }
  vi.stubGlobal("IntersectionObserver", StubObserver);
  return { fire: (isIntersecting) => act(() => callback([{ isIntersecting }])) };
}

describe("RevealSection", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

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

  it("stays not-yet-revealed until the observer reports an intersection", () => {
    const observer = driveIntersectionObserver();
    const { container } = render(
      <RevealSection>
        <p>content</p>
      </RevealSection>,
    );
    const wrapper = container.firstElementChild;
    expect(wrapper?.className.split(/\s+/)).not.toContain("reveal--in");

    observer.fire(true);

    expect(wrapper?.className.split(/\s+/)).toContain("reveal--in");
  });
});
