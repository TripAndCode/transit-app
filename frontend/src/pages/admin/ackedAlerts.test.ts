import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { ackAlert, hashAlertKey, pruneAcked, readAckedAlerts, type AckedMap } from "./ackedAlerts";

const DAY_MS = 24 * 60 * 60 * 1000;

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("hashAlertKey", () => {
  it("is deterministic for the same inputs", () => {
    expect(hashAlertKey("warn", "Toyama Bayline behind", "/admin/ops")).toBe(
      hashAlertKey("warn", "Toyama Bayline behind", "/admin/ops"),
    );
  });

  it("differs when the level differs", () => {
    expect(hashAlertKey("warn", "same text", "/x")).not.toBe(hashAlertKey("info", "same text", "/x"));
  });

  it("differs when the text differs", () => {
    expect(hashAlertKey("warn", "text a", "/x")).not.toBe(hashAlertKey("warn", "text b", "/x"));
  });

  it("differs when the href differs, including null vs a real path", () => {
    expect(hashAlertKey("warn", "same text", "/a")).not.toBe(hashAlertKey("warn", "same text", "/b"));
    expect(hashAlertKey("warn", "same text", null)).not.toBe(hashAlertKey("warn", "same text", "/a"));
  });

  it("does not collide across the level|text|href join boundary", () => {
    expect(hashAlertKey("warn", "a|b", null)).not.toBe(hashAlertKey("warna", "b", null));
  });
});

describe("pruneAcked", () => {
  const now = 1_700_000_000_000;

  it("keeps entries that have not yet expired", () => {
    const map: AckedMap = { a: now + 1 };
    expect(pruneAcked(map, now)).toEqual({ a: now + 1 });
  });

  it("drops entries whose expiry has passed", () => {
    const map: AckedMap = { a: now - 1, b: now + DAY_MS };
    expect(pruneAcked(map, now)).toEqual({ b: now + DAY_MS });
  });

  it("drops an entry expiring at exactly now", () => {
    const map: AckedMap = { a: now };
    expect(pruneAcked(map, now)).toEqual({});
  });
});

describe("ackAlert / readAckedAlerts", () => {
  const now = 1_700_000_000_000;

  it("marks a hash acknowledged for readAckedAlerts to see", () => {
    ackAlert("abc123", now);
    expect(readAckedAlerts(now).has("abc123")).toBe(true);
  });

  it("expires an acknowledgement after 7 days", () => {
    ackAlert("abc123", now);
    expect(readAckedAlerts(now + 7 * DAY_MS - 1).has("abc123")).toBe(true);
    expect(readAckedAlerts(now + 7 * DAY_MS).has("abc123")).toBe(false);
  });

  it("persists across separate reads (backed by localStorage)", () => {
    ackAlert("hash-1", now);
    ackAlert("hash-2", now);
    const acked = readAckedAlerts(now);
    expect(acked.has("hash-1")).toBe(true);
    expect(acked.has("hash-2")).toBe(true);
  });

  it("does not throw when localStorage.setItem throws (private browsing / quota)", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(() => ackAlert("abc123", now)).not.toThrow();
  });

  it("reads as empty when localStorage.getItem throws", () => {
    ackAlert("abc123", now);
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new DOMException("blocked");
    });
    expect(readAckedAlerts(now).size).toBe(0);
  });

  it("treats malformed stored JSON as no acknowledgements", () => {
    localStorage.setItem("transit.admin.ackedAlerts", "{not json");
    expect(readAckedAlerts(now).size).toBe(0);
  });
});
