import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { savedViews, useSavedViews, NAME_MAX } from "./savedViews";
import { blockStorage } from "../test/blockStorage";

describe("savedViews", () => {
  beforeEach(() => {
    localStorage.clear();
  });
  afterEach(() => vi.restoreAllMocks());

  it("creates a view and lists it back for its agency", () => {
    const result = savedViews.create(1, "My view", { dow: "weekday" });
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("unreachable");
    expect(result.view.agency_id).toBe(1);
    expect(result.view.name).toBe("My view");
    expect(result.view.range_ctx).toEqual({ dow: "weekday" });
    expect(typeof result.view.id).toBe("string");
    expect(savedViews.list(1)).toEqual([result.view]);
  });

  it("trims the name and caps it at NAME_MAX characters", () => {
    const result = savedViews.create(1, `  ${"x".repeat(NAME_MAX + 20)}  `, {});
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error("unreachable");
    expect(result.view.name).toHaveLength(NAME_MAX);
  });

  it("lists an agency's views newest first", () => {
    const a = savedViews.create(1, "A", {});
    const b = savedViews.create(1, "B", {});
    if (!a.ok || !b.ok) throw new Error("unreachable");
    expect(savedViews.list(1).map((v) => v.id)).toEqual([b.view.id, a.view.id]);
  });

  it("scopes the list to the given agency", () => {
    savedViews.create(1, "Agency one", {});
    savedViews.create(2, "Agency two", {});
    expect(savedViews.list(1).map((v) => v.name)).toEqual(["Agency one"]);
    expect(savedViews.list(2).map((v) => v.name)).toEqual(["Agency two"]);
  });

  it("refuses a duplicate name for the same agency but allows it for another", () => {
    savedViews.create(1, "Weekdays", {});
    const dup = savedViews.create(1, "Weekdays", {});
    expect(dup).toEqual({ ok: false, reason: "name_taken" });
    const other = savedViews.create(2, "Weekdays", {});
    expect(other.ok).toBe(true);
  });

  it("matches a duplicate name exactly after trimming", () => {
    savedViews.create(1, "Weekdays", {});
    expect(savedViews.create(1, "  Weekdays  ", {})).toEqual({ ok: false, reason: "name_taken" });
    expect(savedViews.create(1, "weekdays", {}).ok).toBe(true);
  });

  it("refuses the 51st view for one agency", () => {
    for (let i = 0; i < 50; i++) {
      expect(savedViews.create(1, `view ${i}`, {}).ok).toBe(true);
    }
    expect(savedViews.create(1, "one too many", {})).toEqual({ ok: false, reason: "full" });
    expect(savedViews.list(1)).toHaveLength(50);
  });

  it("deletes a view", () => {
    const created = savedViews.create(1, "A", {});
    if (!created.ok) throw new Error("unreachable");
    expect(savedViews.remove(created.view.id)).toBe(true);
    expect(savedViews.list(1)).toEqual([]);
  });

  it("keeps a view and reports false when the browser refuses the delete", () => {
    const created = savedViews.create(1, "A", {});
    if (!created.ok) throw new Error("unreachable");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    expect(savedViews.remove(created.view.id)).toBe(false);
    expect(savedViews.list(1)).toEqual([created.view]);
  });

  it("reads corrupt JSON as an empty list", () => {
    localStorage.setItem("transit.savedViews.v1", "not json");
    expect(savedViews.list(1)).toEqual([]);
  });

  it("reads a non-array value as an empty list", () => {
    localStorage.setItem("transit.savedViews.v1", JSON.stringify({ not: "an array" }));
    expect(savedViews.list(1)).toEqual([]);
  });

  it("skips malformed entries instead of throwing", () => {
    const good = { id: "1", agency_id: 1, name: "ok", range_ctx: {}, created_at: "2026-01-01T00:00:00.000Z" };
    localStorage.setItem(
      "transit.savedViews.v1",
      JSON.stringify([good, { id: "2" }, { agency_id: 1, name: "no id", range_ctx: {}, created_at: "x" }, null, "x"]),
    );
    expect(savedViews.list(1)).toEqual([good]);
  });

  it("reports a refused write instead of throwing", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("quota", "QuotaExceededError");
    });
    expect(savedViews.create(1, "A", {})).toEqual({ ok: false, reason: "save_failed" });
  });

  it("reads a blocked store as an empty list", () => {
    blockStorage("localStorage");
    expect(savedViews.list(1)).toEqual([]);
  });

  it("re-renders a mounted useSavedViews hook on a storage event from another tab", () => {
    const { result } = renderHook(() => useSavedViews(1));
    expect(result.current).toEqual([]);
    // Simulate another tab writing directly, bypassing this tab's create():
    // the only way this page learns of it is the storage event below.
    localStorage.setItem(
      "transit.savedViews.v1",
      JSON.stringify([{ id: "x", agency_id: 1, name: "From another tab", range_ctx: {}, created_at: "2026-01-01T00:00:00.000Z" }]),
    );
    act(() => window.dispatchEvent(new Event("storage")));
    expect(result.current.map((v) => v.name)).toEqual(["From another tab"]);
  });
});
