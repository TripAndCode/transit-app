import { describe, it, expect, vi, afterEach } from "vitest";
import { ROUTE_TITLE_TRANSITION, isPlainLeftClick, routeTitleStyle, supportsViewTransition, withViewTransition } from "./viewTransition";
import { stubReducedMotion } from "../test/reducedMotion";

type VTDoc = { startViewTransition?: unknown };
afterEach(() => { vi.restoreAllMocks(); delete ((document as unknown as VTDoc)).startViewTransition; });

describe("withViewTransition", () => {
  it("runs the update inside document.startViewTransition when supported", async () => {
    const update = vi.fn();
    ((document as unknown as VTDoc)).startViewTransition = vi.fn((cb) => { void cb(); return { finished: Promise.resolve() }; });
    await withViewTransition(update);
    expect(((document as unknown as VTDoc)).startViewTransition).toHaveBeenCalledTimes(1);
    expect(update).toHaveBeenCalledTimes(1);
  });
  it("runs the update directly when the API is missing", async () => {
    const update = vi.fn();
    await withViewTransition(update);
    expect(update).toHaveBeenCalledTimes(1);
  });
  it("runs the update directly under reduced motion even when the API exists", async () => {
    stubReducedMotion();
    const start = vi.fn();
    ((document as unknown as VTDoc)).startViewTransition = start;
    const update = vi.fn();
    await withViewTransition(update);
    expect(start).not.toHaveBeenCalled();
    expect(update).toHaveBeenCalledTimes(1);
    expect(supportsViewTransition()).toBe(false);
  });
  it("falls back when the API throws", async () => {
    ((document as unknown as VTDoc)).startViewTransition = vi.fn(() => { throw new Error("busy"); });
    const update = vi.fn();
    await withViewTransition(update);
    expect(update).toHaveBeenCalledTimes(1);
  });
});

describe("routeTitleStyle / isPlainLeftClick", () => {
  it("names the element only while active", () => {
    expect(routeTitleStyle(true)).toEqual({ viewTransitionName: ROUTE_TITLE_TRANSITION });
    expect(routeTitleStyle(false)).toBeUndefined();
  });
  it("leaves modified, secondary and already-handled clicks to the anchor", () => {
    const base = { button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, defaultPrevented: false };
    expect(isPlainLeftClick(base)).toBe(true);
    expect(isPlainLeftClick({ ...base, metaKey: true })).toBe(false);
    expect(isPlainLeftClick({ ...base, button: 1 })).toBe(false);
    expect(isPlainLeftClick({ ...base, defaultPrevented: true })).toBe(false);
  });
});
