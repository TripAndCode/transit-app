import { describe, it, expect, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";
import { isAuthRequired, refreshAuthStateOn401, retryTransientOnce } from "./authExpiry";

const authRequired = () => new ApiError(401, JSON.stringify({ detail: "auth required" }));

describe("isAuthRequired", () => {
  it("matches the 401 the login gate and require_user return", () => {
    expect(isAuthRequired(authRequired())).toBe(true);
  });

  it("ignores other 401s, other statuses and non-API errors", () => {
    expect(isAuthRequired(new ApiError(401, JSON.stringify({ detail: "Invalid API key" })))).toBe(false);
    expect(isAuthRequired(new ApiError(403, JSON.stringify({ detail: "auth required" })))).toBe(false);
    expect(isAuthRequired(new Error("auth required"))).toBe(false);
  });
});

describe("refreshAuthStateOn401", () => {
  it("refetches the session and the forever-cached config on an auth-required 401", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries").mockResolvedValue();
    refreshAuthStateOn401(qc)(authRequired());
    expect(spy).toHaveBeenCalledWith({ queryKey: ["me"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["config"] });
  });

  it("leaves both alone for any other error", () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries").mockResolvedValue();
    refreshAuthStateOn401(qc)(new ApiError(500, ""));
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("retryTransientOnce", () => {
  const body = (detail: string) => JSON.stringify({ detail });

  it("retries a transient failure once: a 5xx or a dropped connection", () => {
    expect(retryTransientOnce(0, new ApiError(500, ""))).toBe(true);
    expect(retryTransientOnce(0, new ApiError(502, "<html>Bad Gateway</html>"))).toBe(true);
    expect(retryTransientOnce(0, new TypeError("Failed to fetch"))).toBe(true);
    expect(retryTransientOnce(1, new ApiError(500, ""))).toBe(false);
    expect(retryTransientOnce(1, new TypeError("Failed to fetch"))).toBe(false);
  });

  it("never retries an auth-required 401", () => {
    expect(retryTransientOnce(0, authRequired())).toBe(false);
  });

  it("never retries a client error, a 404 or a 429", () => {
    expect(retryTransientOnce(0, new ApiError(400, body("bad")))).toBe(false);
    expect(retryTransientOnce(0, new ApiError(403, body("forbidden")))).toBe(false);
    expect(retryTransientOnce(0, new ApiError(404, ""))).toBe(false);
    expect(retryTransientOnce(0, new ApiError(422, ""))).toBe(false);
    expect(retryTransientOnce(0, new ApiError(429, ""))).toBe(false);
  });

  it("never retries a standing condition: not approved, aggregates not ready", () => {
    expect(retryTransientOnce(0, new ApiError(403, body("llm_not_approved")))).toBe(false);
    expect(
      retryTransientOnce(0, new ApiError(503, JSON.stringify({ detail: "x", code: "aggregate_not_ready" }))),
    ).toBe(false);
  });

  it("does not repeat a request that already waited out the full request timeout", () => {
    expect(retryTransientOnce(0, new DOMException("timed out", "TimeoutError"))).toBe(false);
  });
});
