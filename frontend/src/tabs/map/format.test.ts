import { describe, it, expect } from "vitest";
import { hhmm } from "./format";

describe("hhmm", () => {
  it("slices HH:MM off an HH:MM:SS scheduled time", () => {
    expect(hhmm({ scheduled_time: "08:15:00" })).toBe("08:15");
  });

  it("falls back to a placeholder when scheduled_time is null", () => {
    expect(hhmm({ scheduled_time: null })).toBe("--:--");
  });
});
