import { describe, it, expect } from "vitest";
import { ApiError } from "./client";
import { classifyError, isTransientErrorClass } from "./errorClass";

describe("classifyError", () => {
  it("classifies the admin-approval-required 403 as not_approved", () => {
    expect(classifyError(new ApiError(403, JSON.stringify({ detail: "llm_not_approved" })))).toBe("not_approved");
  });

  it("classifies a plain 403 (not the llm_not_approved detail) as generic", () => {
    expect(classifyError(new ApiError(403, JSON.stringify({ detail: "forbidden" })))).toBe("generic");
  });

  it("classifies the aggregate_not_ready 503 as not_ready", () => {
    expect(classifyError(new ApiError(503, JSON.stringify({ code: "aggregate_not_ready" })))).toBe("not_ready");
  });

  it("classifies any other 503 as server", () => {
    expect(classifyError(new ApiError(503, "boom"))).toBe("server");
  });

  it("classifies 429 as rate_limited", () => {
    expect(classifyError(new ApiError(429, ""))).toBe("rate_limited");
  });

  it("classifies 404 as not_found", () => {
    expect(classifyError(new ApiError(404, ""))).toBe("not_found");
  });

  it("classifies 500 as server", () => {
    expect(classifyError(new ApiError(500, "boom"))).toBe("server");
  });

  it("classifies any other ApiError status as generic", () => {
    expect(classifyError(new ApiError(418, ""))).toBe("generic");
  });

  it("classifies a DOMException named TimeoutError as timeout", () => {
    expect(classifyError(new DOMException("timed out", "TimeoutError"))).toBe("timeout");
  });

  it("classifies a plain Error named TimeoutError as timeout", () => {
    const err = new Error("timed out");
    err.name = "TimeoutError";
    expect(classifyError(err)).toBe("timeout");
  });

  it("classifies a TypeError (the shape fetch() throws on a network failure) as network", () => {
    expect(classifyError(new TypeError("Failed to fetch"))).toBe("network");
  });

  it("classifies any other plain Error as network", () => {
    expect(classifyError(new Error("boom"))).toBe("network");
  });

  it("classifies a non-Error value as generic", () => {
    expect(classifyError("boom")).toBe("generic");
    expect(classifyError(null)).toBe("generic");
    expect(classifyError(undefined)).toBe("generic");
  });
});

describe("isTransientErrorClass", () => {
  it("is transient for network and timeout", () => {
    expect(isTransientErrorClass("network")).toBe(true);
    expect(isTransientErrorClass("timeout")).toBe(true);
  });

  it("is not transient for every other class", () => {
    for (const cls of ["not_ready", "not_approved", "not_found", "rate_limited", "server", "generic"] as const) {
      expect(isTransientErrorClass(cls)).toBe(false);
    }
  });
});
