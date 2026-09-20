import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as hooks from "../../api/hooks";
import * as mediaQuery from "../../hooks/useMediaQuery";
import { FRAME_MS } from "./playbackFrames";
import { useDayPlayback } from "./useDayPlayback";
import type { DelayTimelineResponse, TimelineFrame } from "../../api/types";

function frames(count: number): TimelineFrame[] {
  return Array.from({ length: count }, (_, i) => ({
    t: `${String(5 + i).padStart(2, "0")}:00`,
    points: [],
    mean_delay_min: null,
    samples: 0,
  }));
}

function mockTimeline(data: DelayTimelineResponse | undefined) {
  vi.spyOn(hooks, "useTimeline").mockReturnValue({
    data,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof hooks.useTimeline>);
}

function mockReducedMotion(reduce: boolean) {
  vi.spyOn(mediaQuery, "useMediaQuery").mockReturnValue(reduce);
}

beforeEach(() => {
  vi.useFakeTimers();
  mockReducedMotion(false);
  mockTimeline({ date: "2026-03-04", step_minutes: 60, frames: frames(4) });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("useDayPlayback", () => {
  it("rests on the first frame and schedules nothing until asked to play", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    expect(result.current.index).toBe(0);
    expect(result.current.playing).toBe(false);
    act(() => vi.advanceTimersByTime(FRAME_MS * 3));
    expect(result.current.index).toBe(0);
  });

  it("advances one frame per FRAME_MS once playing", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.toggle());
    expect(result.current.playing).toBe(true);
    act(() => vi.advanceTimersByTime(FRAME_MS));
    expect(result.current.index).toBe(1);
    act(() => vi.advanceTimersByTime(FRAME_MS));
    expect(result.current.index).toBe(2);
  });

  it("covers twice the ground per second at 2x", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.cycleSpeed());
    expect(result.current.speed).toBe(2);
    act(() => result.current.toggle());
    act(() => vi.advanceTimersByTime(FRAME_MS));
    expect(result.current.index).toBe(2);
  });

  it("resumes from where it was scrubbed to rather than snapping back", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.toggle());
    act(() => vi.advanceTimersByTime(FRAME_MS * 2));
    act(() => result.current.setIndex(0));
    expect(result.current.index).toBe(0);
    act(() => vi.advanceTimersByTime(FRAME_MS));
    expect(result.current.index).toBe(1);
  });

  it("stops playing when a frame is stepped by hand", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.toggle());
    act(() => result.current.step(1));
    expect(result.current.playing).toBe(false);
    expect(result.current.index).toBe(1);
    act(() => vi.advanceTimersByTime(FRAME_MS * 3));
    expect(result.current.index).toBe(1);
  });

  it("wraps a step at both ends of the day", () => {
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.step(-1));
    expect(result.current.index).toBe(3);
    act(() => result.current.step(1));
    expect(result.current.index).toBe(0);
  });

  it("never auto-advances under reduced motion", () => {
    mockReducedMotion(true);
    const { result } = renderHook(() => useDayPlayback(1, true));
    expect(result.current.steppingOnly).toBe(true);
    act(() => result.current.toggle());
    expect(result.current.playing).toBe(false);
    act(() => vi.advanceTimersByTime(FRAME_MS * 5));
    expect(result.current.index).toBe(0);
  });

  it("still steps under reduced motion", () => {
    mockReducedMotion(true);
    const { result } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.step(1));
    expect(result.current.index).toBe(1);
  });

  it("pulls the playhead back inside a shorter day rather than pointing past its end", () => {
    const { result, rerender } = renderHook(() => useDayPlayback(1, true));
    act(() => result.current.setIndex(3));
    expect(result.current.index).toBe(3);
    mockTimeline({ date: "2026-03-05", step_minutes: 60, frames: frames(2) });
    rerender();
    expect(result.current.index).toBe(1);
  });

  it("has nothing to play before the day arrives", () => {
    mockTimeline(undefined);
    const { result } = renderHook(() => useDayPlayback(1, true));
    expect(result.current.frames).toEqual([]);
    expect(result.current.date).toBeNull();
    act(() => result.current.toggle());
    expect(result.current.playing).toBe(false);
  });
});
