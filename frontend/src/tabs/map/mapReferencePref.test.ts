import { beforeEach, describe, expect, it } from "vitest";
import { blockStorage } from "../../test/blockStorage";
import { readMapReferencePref, writeMapReferencePref } from "./mapReferencePref";

describe("map reference panel preference", () => {
  beforeEach(() => localStorage.clear());

  it("starts shown and keeps a dismissal", () => {
    expect(readMapReferencePref()).toBe(true);
    writeMapReferencePref(false);
    expect(readMapReferencePref()).toBe(false);
  });

  it("reads shown and drops the write where site data is blocked", () => {
    blockStorage("localStorage");
    expect(() => writeMapReferencePref(false)).not.toThrow();
    expect(readMapReferencePref()).toBe(true);
  });
});
