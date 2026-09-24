import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { createElement } from "react";
import { useRevealOnScroll } from "./useRevealOnScroll";

type FakeObserverCtor = new (callback: IntersectionObserverCallback) => IntersectionObserver;

function stubIntersectionObserver(): { fire: (isIntersecting: boolean) => void; observe: ReturnType<typeof vi.fn> } {
  let capturedCallback: IntersectionObserverCallback | null = null;
  const observe = vi.fn();
  class FakeIntersectionObserver {
    observe = observe;
    disconnect = vi.fn();
    unobserve = vi.fn();
    takeRecords = vi.fn(() => []);
    root = null;
    rootMargin = "";
    thresholds: number[] = [];
    constructor(callback: IntersectionObserverCallback) {
      capturedCallback = callback;
    }
  }
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver as unknown as FakeObserverCtor);
  return {
    observe,
    fire: (isIntersecting: boolean) => {
      capturedCallback?.(
        [{ isIntersecting } as IntersectionObserverEntry],
        new FakeIntersectionObserver(() => {}) as unknown as IntersectionObserver,
      );
    },
  };
}

/** Mounts the hook against a real DOM node, the way JSX's `ref={ref}` would --
 *  unlike `renderHook` alone, this makes `ref.current` non-null by the time
 *  the hook's own effect runs on mount. */
function Probe({ onRevealed }: { onRevealed: (revealed: boolean) => void }) {
  const [ref, revealed] = useRevealOnScroll<HTMLDivElement>();
  onRevealed(revealed);
  return createElement("div", { ref });
}

describe("useRevealOnScroll", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts revealed when IntersectionObserver is unavailable", () => {
    // jsdom has no IntersectionObserver -- this is the real default here,
    // not a contrived case. With no observer to clear the pending offset,
    // the section must never enter it in the first place.
    expect(typeof IntersectionObserver).toBe("undefined");
    const { result } = renderHook(() => useRevealOnScroll<HTMLDivElement>());
    expect(result.current[1]).toBe(true);
  });

  it("observes the attached element and reveals once it intersects", () => {
    const { fire, observe } = stubIntersectionObserver();
    let latest = false;
    render(createElement(Probe, { onRevealed: (r) => { latest = r; } }));
    expect(observe).toHaveBeenCalledTimes(1);
    expect(latest).toBe(false);

    act(() => fire(true));
    expect(latest).toBe(true);
  });

  it("never reveals when the element does not intersect", () => {
    const { fire } = stubIntersectionObserver();
    let latest = false;
    render(createElement(Probe, { onRevealed: (r) => { latest = r; } }));

    act(() => fire(false));
    expect(latest).toBe(false);
  });
});
