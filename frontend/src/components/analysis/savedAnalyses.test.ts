import { describe, it, expect, beforeEach, vi } from "vitest";
import { readAnalyses, saveAnalysis, deleteAnalysis } from "./savedAnalyses";
import type { RangeCtx } from "../../api/rangeContext";

const ctx: RangeCtx = { from: "2026-01-01", to: "2026-01-31", dow: "all", time_band: "all", service: "all", routes: [] };

describe("savedAnalyses (localStorage)", () => {
  beforeEach(() => localStorage.clear());

  it("round-trips a saved analysis", () => {
    expect(saveAnalysis(1, "Coast mornings", ctx, false)).toBe(true);
    const rows = readAnalyses();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ agencyId: 1, title: "Coast mornings" });
  });

  it("deletes a saved analysis", () => {
    saveAnalysis(1, "Coast mornings", ctx, false);
    const [row] = readAnalyses();
    expect(deleteAnalysis(row.id)).toBe(true);
    expect(readAnalyses()).toHaveLength(0);
  });

  it("returns false instead of throwing when localStorage.setItem fails on save", () => {
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => saveAnalysis(1, "Coast mornings", ctx, false)).not.toThrow();
    expect(saveAnalysis(1, "Coast mornings", ctx, false)).toBe(false);
    spy.mockRestore();
  });

  it("returns false instead of throwing when localStorage.setItem fails on delete", () => {
    saveAnalysis(1, "Coast mornings", ctx, false);
    const [row] = readAnalyses();
    const spy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("localStorage unavailable");
    });
    expect(() => deleteAnalysis(row.id)).not.toThrow();
    expect(deleteAnalysis(row.id)).toBe(false);
    spy.mockRestore();
  });

  it("falls back to a non-crypto id when crypto.randomUUID is unavailable", () => {
    const original = crypto.randomUUID;
    // @ts-expect-error -- simulating a context without the secure-context-only API
    delete crypto.randomUUID;
    expect(saveAnalysis(1, "Coast mornings", ctx, false)).toBe(true);
    expect(readAnalyses()[0].id).toEqual(expect.any(String));
    crypto.randomUUID = original;
  });
});
