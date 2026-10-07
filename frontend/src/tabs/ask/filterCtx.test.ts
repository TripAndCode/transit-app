import { afterEach, describe, expect, it, vi } from "vitest";
import { resolvedFilterCtx } from "./filterCtx";

describe("resolvedFilterCtx", () => {
  afterEach(() => vi.useRealTimers());

  it("fills a stored filter's missing dates with the default period, which stops at the data", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-02T14:59:59Z")); // 2026-10-02 23:59:59 JST
    const fc = resolvedFilterCtx({ dow: "weekend" }, "2026-09-28");
    expect(fc.from_date).toBe("2026-08-30");
    expect(fc.to_date).toBe("2026-09-28");
    expect(fc.dow).toBe("weekend");
  });

  it("keeps the dates a stored filter does carry", () => {
    const fc = resolvedFilterCtx({ from_date: "2026-06-01", to_date: "2026-06-30" }, "2026-09-28");
    expect(fc.from_date).toBe("2026-06-01");
    expect(fc.to_date).toBe("2026-06-30");
  });
});
