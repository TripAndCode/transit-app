import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useRef } from "react";
import { useInView } from "./useInView";

type ObserverCallback = (entries: { isIntersecting: boolean }[]) => void;

/** Installs a stub IntersectionObserver and captures its callback/options so
 *  a test can fire an intersection directly -- jsdom implements no
 *  IntersectionObserver at all, so there is nothing real to drive here. */
function mockIntersectionObserver() {
  let lastCallback: ObserverCallback = () => {};
  let lastOptions: IntersectionObserverInit | undefined;
  const observe = vi.fn();
  const disconnect = vi.fn();

  class Stub {
    constructor(cb: ObserverCallback, options?: IntersectionObserverInit) {
      lastCallback = cb;
      lastOptions = options;
    }
    observe = observe;
    disconnect = disconnect;
    unobserve = vi.fn();
    takeRecords = () => [];
    root = null;
    rootMargin = "";
    thresholds: number[] = [];
  }

  vi.stubGlobal("IntersectionObserver", Stub);
  return {
    fire(isIntersecting: boolean) {
      act(() => lastCallback([{ isIntersecting }]));
    },
    observe,
    disconnect,
    get options() {
      return lastOptions;
    },
  };
}

function useHarness(rootMargin?: string) {
  const ref = useRef<HTMLDivElement>(document.createElement("div"));
  return useInView(ref, rootMargin ? { rootMargin } : undefined);
}

describe("useInView", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("starts false and observes the element", () => {
    const observer = mockIntersectionObserver();
    const { result } = renderHook(() => useHarness());
    expect(result.current).toBe(false);
    expect(observer.observe).toHaveBeenCalledTimes(1);
  });

  it("becomes true once the element intersects, and disconnects", () => {
    const observer = mockIntersectionObserver();
    const { result } = renderHook(() => useHarness());
    observer.fire(true);
    expect(result.current).toBe(true);
    expect(observer.disconnect).toHaveBeenCalledTimes(1);
  });

  it("does not flip back to false on a later non-intersecting callback -- a one-shot reveal, not a live tracker", () => {
    const observer = mockIntersectionObserver();
    const { result } = renderHook(() => useHarness());
    observer.fire(true);
    observer.fire(false);
    expect(result.current).toBe(true);
  });

  it("defaults rootMargin to -8%", () => {
    const observer = mockIntersectionObserver();
    renderHook(() => useHarness());
    expect(observer.options?.rootMargin).toBe("-8%");
  });

  it("passes a caller-supplied rootMargin through", () => {
    const observer = mockIntersectionObserver();
    renderHook(() => useHarness("-10% 0px"));
    expect(observer.options?.rootMargin).toBe("-10% 0px");
  });

  it("is true immediately when IntersectionObserver is unavailable, so a section is never stuck pre-reveal", () => {
    vi.stubGlobal("IntersectionObserver", undefined);
    const { result } = renderHook(() => useHarness());
    expect(result.current).toBe(true);
  });
});
