import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useEnteredOnMount } from "./useEnteredOnMount";

describe("useEnteredOnMount", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("is false on the initial render", () => {
    vi.spyOn(window, "requestAnimationFrame").mockImplementation(() => 1);
    const { result } = renderHook(() => useEnteredOnMount());
    expect(result.current).toBe(false);
  });

  it("becomes true once the post-mount frame runs", () => {
    let cb: FrameRequestCallback = () => {};
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((fn) => {
      cb = fn;
      return 1;
    });
    const { result } = renderHook(() => useEnteredOnMount());
    expect(result.current).toBe(false);
    act(() => cb(0));
    expect(result.current).toBe(true);
  });
});
