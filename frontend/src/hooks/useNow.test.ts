import { describe, it, expect, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useNow } from "./useNow";

describe("useNow", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reads the clock again every interval, and stops when unmounted", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-01T00:00:00Z"));
    const { result, unmount } = renderHook(() => useNow(30_000));
    expect(result.current.toISOString()).toBe("2026-10-01T00:00:00.000Z");

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.toISOString()).toBe("2026-10-01T00:00:30.000Z");

    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
