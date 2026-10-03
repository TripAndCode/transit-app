import { describe, it, expect, vi, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { AUTO_RETRY_DELAYS_MS, useAutoRetry } from "./useAutoRetry";

describe("useAutoRetry", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("is not retrying when there is no error", () => {
    const { result } = renderHook(() => useAutoRetry(false, false, vi.fn()));
    expect(result.current.retrying).toBe(false);
  });

  it("is not retrying for a non-transient error, and never schedules a timer", () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    const { result } = renderHook(() => useAutoRetry(true, false, onRetry));
    expect(result.current.retrying).toBe(false);
    vi.advanceTimersByTime(60_000);
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("is not retrying when there is no onRetry to call", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useAutoRetry(true, true, undefined));
    expect(result.current.retrying).toBe(false);
    vi.advanceTimersByTime(60_000);
  });

  it("quietly retries a transient error twice on backoff, then stops", () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    const { result, rerender } = renderHook(
      ({ hasError, onRetry }) => useAutoRetry(hasError, true, onRetry),
      { initialProps: { hasError: true, onRetry } },
    );
    expect(result.current.retrying).toBe(true);
    expect(onRetry).not.toHaveBeenCalled();

    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[0]);
    rerender({ hasError: true, onRetry });
    expect(onRetry).toHaveBeenCalledTimes(1);
    expect(result.current.retrying).toBe(true);

    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[1]);
    rerender({ hasError: true, onRetry });
    expect(onRetry).toHaveBeenCalledTimes(2);
    // Both attempts spent -- no more auto-retries, hand off to a manual one.
    expect(result.current.retrying).toBe(false);

    vi.advanceTimersByTime(60_000);
    expect(onRetry).toHaveBeenCalledTimes(2);
  });

  it("resets the attempt count once the error clears, so a later failure gets its own two attempts", () => {
    vi.useFakeTimers();
    const onRetry = vi.fn();
    const { result, rerender } = renderHook(
      ({ hasError }) => useAutoRetry(hasError, true, onRetry),
      { initialProps: { hasError: true } },
    );
    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[0]);
    rerender({ hasError: true });
    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[1]);
    rerender({ hasError: true });
    expect(onRetry).toHaveBeenCalledTimes(2);
    expect(result.current.retrying).toBe(false);

    // Success clears the error ...
    rerender({ hasError: false });
    // ... then a brand-new failure gets its own quiet attempts again.
    rerender({ hasError: true });
    expect(result.current.retrying).toBe(true);
    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[0]);
    rerender({ hasError: true });
    expect(onRetry).toHaveBeenCalledTimes(3);
  });

  it("does not restart the delay timer just because onRetry's identity changes between renders", () => {
    vi.useFakeTimers();
    const calls: string[] = [];
    const { rerender } = renderHook(
      ({ hasError, tag }) => useAutoRetry(hasError, true, () => calls.push(tag)),
      { initialProps: { hasError: true, tag: "a" } },
    );
    // Simulate unrelated parent re-renders producing a fresh onRetry closure
    // before the delay elapses -- this must not push the retry back out.
    rerender({ hasError: true, tag: "b" });
    rerender({ hasError: true, tag: "c" });
    vi.advanceTimersByTime(AUTO_RETRY_DELAYS_MS[0]);
    expect(calls).toEqual(["c"]);
  });
});
