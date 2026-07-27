import { describe, it, expect, afterEach, vi } from "vitest";
import { isToday, isYesterday } from "./threadDateBuckets";

describe("threadDateBuckets (JST-pinned)", () => {
  afterEach(() => vi.useRealTimers());

  it("buckets a timestamp just after JST midnight as today, even on a machine in an earlier local timezone", () => {
    // 2026-07-15 00:30 JST = 2026-07-14 15:30 UTC. A viewer on a machine set
    // to UTC (or anything west of JST) would, using local-timezone
    // comparison, see this as "yesterday" relative to a `now` also taken in
    // that local zone — but the JST-pinned convention this app uses
    // everywhere else says it's already "today" in JST.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T16:00:00Z")); // 2026-07-15 01:00 JST
    expect(isToday("2026-07-14T15:30:00Z")).toBe(true); // 2026-07-15 00:30 JST
  });

  it("buckets a timestamp from JST-yesterday as yesterday, not today", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T16:00:00Z")); // 2026-07-15 01:00 JST
    expect(isYesterday("2026-07-13T20:00:00Z")).toBe(true); // 2026-07-14 05:00 JST
    expect(isToday("2026-07-13T20:00:00Z")).toBe(false);
  });
});
