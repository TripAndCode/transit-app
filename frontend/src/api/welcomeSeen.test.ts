import { describe, it, expect, beforeEach, vi } from "vitest";
import { readWelcomeSeen, writeWelcomeSeen, resetWelcomeSeenMemoryForTests } from "./welcomeSeen";

describe("welcomeSeen (localStorage)", () => {
  beforeEach(() => {
    localStorage.clear();
    resetWelcomeSeenMemoryForTests();
  });

  it("returns 'unseen' when nothing stored", () => {
    expect(readWelcomeSeen()).toBe("unseen");
  });

  it("returns 'seen' after writing", () => {
    writeWelcomeSeen();
    expect(readWelcomeSeen()).toBe("seen");
  });

  it("returns 'unavailable' (not 'unseen') when localStorage.getItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readWelcomeSeen()).toBe("unavailable");
    spy.mockRestore();
  });

  it("doesn't throw when localStorage.setItem throws", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => writeWelcomeSeen()).not.toThrow();
    spy.mockRestore();
  });

  it("reports 'unavailable' (not 'unseen') on the next read after a setItem-only failure", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(readWelcomeSeen()).toBe("unseen");
    writeWelcomeSeen();
    spy.mockRestore();
    expect(readWelcomeSeen()).toBe("unavailable");
  });
});
