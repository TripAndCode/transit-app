// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, apiPatch, isAggregateNotReady } from "./client";

describe("isAggregateNotReady", () => {
  it("is true for a 503 with the aggregate_not_ready code", () => {
    const err = new ApiError(503, JSON.stringify({ detail: "x", code: "aggregate_not_ready" }));
    expect(isAggregateNotReady(err)).toBe(true);
  });

  it("is false for a 503 without that code", () => {
    expect(isAggregateNotReady(new ApiError(503, JSON.stringify({ detail: "down" })))).toBe(false);
  });

  it("is false for other statuses and non-ApiError values", () => {
    expect(isAggregateNotReady(new ApiError(500, JSON.stringify({ code: "aggregate_not_ready" })))).toBe(false);
    expect(isAggregateNotReady(new Error("boom"))).toBe(false);
    expect(isAggregateNotReady(null)).toBe(false);
  });

  it("is false when the body is not JSON", () => {
    expect(isAggregateNotReady(new ApiError(503, "Service Unavailable"))).toBe(false);
  });
});

describe("apiPatch", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("forwards an AbortSignal through to fetch, like apiGet/apiPost already do", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    // rawFetch reads an optional API key out of localStorage — this file's
    // `node` environment (unlike the suite's default jsdom) has no such
    // global at all, unrelated to the signal behavior under test here.
    vi.stubGlobal("localStorage", { getItem: () => null });
    const controller = new AbortController();

    await apiPatch("/api/admin/users/1", { role: "admin" }, { signal: controller.signal });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/admin/users/1"),
      expect.objectContaining({ signal: controller.signal }),
    );
  });
});
