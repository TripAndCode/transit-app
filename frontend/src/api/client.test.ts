import { describe, it, expect } from "vitest";
import { ApiError, isAggregateNotReady, isAnonAskQuotaExceeded } from "./client";

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

describe("isAnonAskQuotaExceeded", () => {
  it("is true for a 429 with the ask_anon_quota_exceeded code", () => {
    const err = new ApiError(429, JSON.stringify({ detail: "x", code: "ask_anon_quota_exceeded" }));
    expect(isAnonAskQuotaExceeded(err)).toBe(true);
  });

  it("is false for a 429 without that code", () => {
    expect(isAnonAskQuotaExceeded(new ApiError(429, JSON.stringify({ detail: "too many requests" })))).toBe(
      false,
    );
  });

  it("is false for other statuses and non-ApiError values", () => {
    expect(
      isAnonAskQuotaExceeded(new ApiError(500, JSON.stringify({ code: "ask_anon_quota_exceeded" }))),
    ).toBe(false);
    expect(isAnonAskQuotaExceeded(new Error("boom"))).toBe(false);
    expect(isAnonAskQuotaExceeded(null)).toBe(false);
  });

  it("is false when the body is not JSON", () => {
    expect(isAnonAskQuotaExceeded(new ApiError(429, "Too Many Requests"))).toBe(false);
  });
});
