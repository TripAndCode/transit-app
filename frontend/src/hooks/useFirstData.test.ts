import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useFirstData } from "./useFirstData";
import { stubReducedMotion } from "../test/reducedMotion";

afterEach(() => vi.restoreAllMocks());

describe("useFirstData", () => {
  it("stays false while there is no data, even across frames", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return frames.length; });
    const { result } = renderHook(({ has }) => useFirstData(has), { initialProps: { has: false } });
    expect(result.current).toBe(false);
    expect(frames).toHaveLength(0);
  });

  it("flips to true one frame after data first arrives, and stays true when data later empties", () => {
    const frames: FrameRequestCallback[] = [];
    vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { frames.push(cb); return frames.length; });
    const { result, rerender } = renderHook(({ has }) => useFirstData(has), { initialProps: { has: false } });
    rerender({ has: true });
    expect(result.current).toBe(false);
    act(() => frames.splice(0).forEach((cb) => cb(0)));
    expect(result.current).toBe(true);
    rerender({ has: false });
    expect(result.current).toBe(true);
    rerender({ has: true });
    expect(frames).toHaveLength(0);
  });

  it("is true from the first render under reduced motion", () => {
    stubReducedMotion();
    const raf = vi.spyOn(window, "requestAnimationFrame");
    const { result } = renderHook(() => useFirstData(false));
    expect(result.current).toBe(true);
    expect(raf).not.toHaveBeenCalled();
  });
});
