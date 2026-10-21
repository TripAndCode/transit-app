import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { blockStorage } from "../test/blockStorage";
import { readStorage, removeStorage, withStorage, writeStorage } from "./safeStorage";

const AREAS = ["localStorage", "sessionStorage"] as const;

/** Some browsers refuse at the property itself: reading `window.localStorage`
 *  throws before any method can be called. */
function blockPropertyAccess(area: (typeof AREAS)[number]): () => void {
  const original = Object.getOwnPropertyDescriptor(globalThis, area);
  Object.defineProperty(globalThis, area, {
    configurable: true,
    get() {
      throw new DOMException("The operation is insecure.", "SecurityError");
    },
  });
  return () => {
    if (original) Object.defineProperty(globalThis, area, original);
  };
}

describe.each(AREAS)("safe storage over %s", (area) => {
  beforeEach(() => globalThis[area].clear());
  afterEach(() => {
    vi.unstubAllGlobals();
    globalThis[area].clear();
  });

  it("reads, writes and removes like the store itself while it works", () => {
    expect(readStorage(area, "k")).toBeNull();
    expect(writeStorage(area, "k", "v")).toBe(true);
    expect(globalThis[area].getItem("k")).toBe("v");
    expect(readStorage(area, "k")).toBe("v");
    expect(removeStorage(area, "k")).toBe(true);
    expect(globalThis[area].getItem("k")).toBeNull();
    expect(withStorage(area, (storage) => storage.length, -1)).toBe(0);
  });

  it("reads nothing and reports a refused write or remove when every call throws", () => {
    blockStorage(area);
    expect(readStorage(area, "k")).toBeNull();
    expect(writeStorage(area, "k", "v")).toBe(false);
    expect(removeStorage(area, "k")).toBe(false);
    expect(withStorage(area, (storage) => storage.getItem("k"), "fallback")).toBe("fallback");
  });

  it("reads nothing and reports a refused write or remove when reaching the store throws", () => {
    const restore = blockPropertyAccess(area);
    try {
      expect(readStorage(area, "k")).toBeNull();
      expect(writeStorage(area, "k", "v")).toBe(false);
      expect(removeStorage(area, "k")).toBe(false);
      expect(withStorage(area, (storage) => storage.getItem("k"), "fallback")).toBe("fallback");
    } finally {
      restore();
    }
  });

  it("returns the fallback when the caller's own use of the value throws", () => {
    globalThis[area].setItem("k", "not json");
    expect(withStorage(area, (storage) => JSON.parse(storage.getItem("k") ?? "null") as unknown, "fallback")).toBe("fallback");
  });
});
