import { describe, it, expect } from "vitest";
import { ApiError, isAggregateNotReady } from "./client";

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
