import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { MAX_PINNED_ROUTES, togglePinnedRoute, usePinnedRoutes } from "./pinnedRoutes";
import { blockStorage } from "../test/blockStorage";

const KEY = "transit.pinnedRoutes";

describe("pinned routes", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it("pins a route for its agency alone, and unpins it on a second toggle", () => {
    const nine = renderHook(() => usePinnedRoutes(9));
    const eight = renderHook(() => usePinnedRoutes(8));
    act(() => togglePinnedRoute(9, "21-1"));
    expect(nine.result.current).toEqual(["21-1"]);
    expect(eight.result.current).toEqual([]);
    act(() => togglePinnedRoute(9, "21-1"));
    expect(nine.result.current).toEqual([]);
  });

  it("keeps pins in the order they were made, across a reload", () => {
    act(() => {
      togglePinnedRoute("9", "50");
      togglePinnedRoute("9", "24");
    });
    expect(JSON.parse(localStorage.getItem(KEY) ?? "{}")).toEqual({ "9": ["50", "24"] });
    expect(renderHook(() => usePinnedRoutes(9)).result.current).toEqual(["50", "24"]);
  });

  it("refuses a pin past the cap rather than dropping an older one", () => {
    const codes = Array.from({ length: MAX_PINNED_ROUTES + 1 }, (_, i) => `R${i}`);
    act(() => codes.forEach((code) => togglePinnedRoute(9, code)));
    expect(renderHook(() => usePinnedRoutes(9)).result.current).toEqual(codes.slice(0, MAX_PINNED_ROUTES));
  });

  it("reads unusable stored pins as none", () => {
    localStorage.setItem(KEY, "not json");
    expect(renderHook(() => usePinnedRoutes(9)).result.current).toEqual([]);
    localStorage.setItem(KEY, JSON.stringify({ "9": ["50", 7, null] }));
    expect(renderHook(() => usePinnedRoutes(9)).result.current).toEqual(["50"]);
  });

  it("keeps the page's pins, and pins more, once storage is blocked", () => {
    act(() => togglePinnedRoute(9, "50"));
    blockStorage("localStorage");
    const nine = renderHook(() => usePinnedRoutes(9));
    expect(nine.result.current).toEqual(["50"]);
    act(() => togglePinnedRoute(9, "24"));
    expect(nine.result.current).toEqual(["50", "24"]);
  });

  it("has no pins outside any agency", () => {
    expect(renderHook(() => usePinnedRoutes(null)).result.current).toEqual([]);
  });
});
