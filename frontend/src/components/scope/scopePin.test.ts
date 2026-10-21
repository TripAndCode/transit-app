import { beforeEach, describe, expect, it } from "vitest";
import { blockStorage } from "../../test/blockStorage";
import { readScopePinned, writeScopePinned } from "./scopePin";

describe("scope pin preference", () => {
  beforeEach(() => localStorage.clear());

  it("starts unpinned and round-trips a write", () => {
    expect(readScopePinned()).toBe(false);
    writeScopePinned(true);
    expect(readScopePinned()).toBe(true);
  });

  it("reads unpinned and drops the write where site data is blocked", () => {
    blockStorage("localStorage");
    expect(() => writeScopePinned(true)).not.toThrow();
    expect(readScopePinned()).toBe(false);
  });
});
