import { describe, it, expect, vi } from "vitest";
import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./client";
import { isAuthRequired, refreshAuthStateOn401, retryUnlessAuthRequired } from "./authExpiry";

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

describe("retryUnlessAuthRequired", () => {
  it("never retries an auth-required 401 and retries anything else once", () => {
    expect(retryUnlessAuthRequired(0, authRequired())).toBe(false);
    expect(retryUnlessAuthRequired(0, new ApiError(500, ""))).toBe(true);
    expect(retryUnlessAuthRequired(1, new ApiError(500, ""))).toBe(false);
  });
});
