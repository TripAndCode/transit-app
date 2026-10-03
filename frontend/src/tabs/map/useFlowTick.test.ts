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
