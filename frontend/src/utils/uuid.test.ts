import { describe, it, expect, afterEach, vi } from "vitest";
import { uuid } from "./uuid";

describe("uuid", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns a well-formed UUID via crypto.randomUUID", () => {
    expect(uuid()).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
  });

  it("returns distinct values across calls", () => {
    expect(uuid()).not.toBe(uuid());
  });

  it("falls back to a timestamp/random id when crypto.randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {});
    expect(uuid()).toMatch(/^\d+-0\.\d+$/);
  });
});
