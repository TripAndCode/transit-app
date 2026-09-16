import { beforeEach, describe, expect, it } from "vitest";
import { clampQueueWidth, DEFAULT_QUEUE_WIDTH, MAX_QUEUE_WIDTH, MIN_QUEUE_WIDTH, readQueueWidth, storeQueueWidth } from "./queueWidth";

describe("clampQueueWidth", () => {
  it("keeps a width inside the allowed range", () => {
    expect(clampQueueWidth(400)).toBe(400);
  });

  it("clamps past either bound rather than letting the panel collapse or swallow the map", () => {
    expect(clampQueueWidth(-500)).toBe(MIN_QUEUE_WIDTH);
    expect(clampQueueWidth(5000)).toBe(MAX_QUEUE_WIDTH);
  });

  it("rounds to whole pixels", () => {
    expect(clampQueueWidth(430.6)).toBe(431);
  });
});

describe("readQueueWidth", () => {
  beforeEach(() => localStorage.clear());

  it("falls back to the default when nothing is stored", () => {
    expect(readQueueWidth()).toBe(DEFAULT_QUEUE_WIDTH);
  });

  it("round-trips a stored width", () => {
    storeQueueWidth(512);
    expect(readQueueWidth()).toBe(512);
  });

  it("ignores a non-numeric or nonsensical stored value", () => {
    localStorage.setItem("ops.queueWidth", "wide-please");
    expect(readQueueWidth()).toBe(DEFAULT_QUEUE_WIDTH);
    localStorage.setItem("ops.queueWidth", "0");
    expect(readQueueWidth()).toBe(DEFAULT_QUEUE_WIDTH);
  });

  it("clamps a stored width that is out of range", () => {
    localStorage.setItem("ops.queueWidth", "9999");
    expect(readQueueWidth()).toBe(MAX_QUEUE_WIDTH);
  });
});
