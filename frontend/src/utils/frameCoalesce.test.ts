import { describe, it, expect, vi, afterEach } from "vitest";
import { coalesceToFrame } from "./frameCoalesce";

afterEach(() => {
  vi.useRealTimers();
});

describe("coalesceToFrame", () => {
  it("runs the function once per frame however many times it is scheduled", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame"] });
    const fn = vi.fn();
    const { schedule } = coalesceToFrame(fn);
    schedule();
    schedule();
    schedule();
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersToNextFrame();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("schedules again after the frame has run", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame"] });
    const fn = vi.fn();
    const { schedule } = coalesceToFrame(fn);
    schedule();
    vi.advanceTimersToNextFrame();
    schedule();
    vi.advanceTimersToNextFrame();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("cancel drops a pending frame", () => {
    vi.useFakeTimers({ toFake: ["requestAnimationFrame", "cancelAnimationFrame"] });
    const fn = vi.fn();
    const { schedule, cancel } = coalesceToFrame(fn);
    schedule();
    cancel();
    vi.advanceTimersToNextFrame();
    expect(fn).not.toHaveBeenCalled();
  });
});
