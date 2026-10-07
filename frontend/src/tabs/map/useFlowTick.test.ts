import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useFlowTick } from "./useFlowTick";

afterEach(() => vi.restoreAllMocks());

function fakeFrames() {
  const queue: FrameRequestCallback[] = [];
  vi.spyOn(window, "requestAnimationFrame").mockImplementation((cb) => { queue.push(cb); return queue.length; });
  const cancel = vi.spyOn(window, "cancelAnimationFrame").mockImplementation(() => {});
  return { fire: (now: number) => queue.splice(0).forEach((cb) => cb(now)), cancel };
}

describe("useFlowTick", () => {
  it("calls back at most once per interval, with elapsed time since the loop started", () => {
    const frames = fakeFrames();
    vi.spyOn(performance, "now").mockReturnValue(1000);
    const onTick = vi.fn();
    renderHook(() => useFlowTick(true, 100, onTick));
    frames.fire(1000); frames.fire(1050); frames.fire(1100); frames.fire(1180); frames.fire(1200);
    expect(onTick.mock.calls.map((c) => c[0])).toEqual([0, 100, 200]);
  });
  it("ticks on a shared time grid, so loops started apart write in the same frame", () => {
    const frames = fakeFrames();
    const now = vi.spyOn(performance, "now").mockReturnValue(990);
    const a = vi.fn();
    const b = vi.fn();
    renderHook(() => useFlowTick(true, 100, a));
    frames.fire(1000);
    now.mockReturnValue(1020);
    renderHook(() => useFlowTick(true, 100, b));
    for (const t of [1030, 1100, 1150, 1200]) frames.fire(t);
    // b's clock starts on its first frame; after that both write at 1100 and 1200.
    expect(b.mock.calls.map((c) => c[0])).toEqual([0, 70, 170]);
    expect(a.mock.calls.map((c) => c[0])).toEqual([0, 100, 200]);
  });
  it("never reports negative elapsed time when a frame stamp precedes the loop's start", () => {
    const frames = fakeFrames();
    vi.spyOn(performance, "now").mockReturnValue(1005);
    const onTick = vi.fn();
    renderHook(() => useFlowTick(true, 100, onTick));
    frames.fire(1000);
    expect(onTick.mock.calls[0][0]).toBe(0);
  });
  it("schedules nothing while inactive and cancels on unmount", () => {
    const frames = fakeFrames();
    const { rerender, unmount } = renderHook(({ a }) => useFlowTick(a, 100, vi.fn()), { initialProps: { a: false } });
    expect(window.requestAnimationFrame).not.toHaveBeenCalled();
    rerender({ a: true });
    expect(window.requestAnimationFrame).toHaveBeenCalledTimes(1);
    unmount();
    expect(frames.cancel).toHaveBeenCalled();
  });
});
