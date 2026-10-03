import type { TFunction } from "i18next";
import { describe, it, expect } from "vitest";
import { hhmm, quietFor } from "./format";

describe("hhmm", () => {
  it("slices HH:MM off an HH:MM:SS scheduled time", () => {
    expect(hhmm({ scheduled_time: "08:15:00" })).toBe("08:15");
  });

  it("falls back to a placeholder when scheduled_time is null", () => {
    expect(hhmm({ scheduled_time: null })).toBe("--:--");
  });
});

describe("quietFor", () => {
  const t = ((key: string, values: Record<string, number>) => `${key}:${values.n ?? values.count}`) as unknown as TFunction;

  it("counts minutes within the hour, hours within the day, then days", () => {
    expect(quietFor(43 * 60_000, t)).toBe("operations.quiet_for.minutes:43");
    expect(quietFor(5 * 3_600_000 + 59 * 60_000, t)).toBe("operations.quiet_for.hours:5");
    expect(quietFor(4 * 86_400_000 + 12 * 3_600_000, t)).toBe("operations.quiet_for.days:4");
  });
});
