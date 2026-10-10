// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import i18n from "../i18n";
import { ApiError, apiPatch, formatApiError, isAggregateNotReady } from "./client";

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

describe("formatApiError", () => {
  it("returns a string detail as is", () => {
    expect(formatApiError(new ApiError(409, JSON.stringify({ detail: "agency name taken" })))).toBe("agency name taken");
  });

  it("joins the msg fields of a FastAPI 422 validation list instead of printing the JSON", () => {
    const body = JSON.stringify({
      detail: [
        { type: "int_parsing", loc: ["query", "actor"], msg: "Input should be a valid integer", input: "abc" },
        { type: "missing", loc: ["query", "from"], msg: "Field required" },
      ],
    });
    expect(formatApiError(new ApiError(422, body))).toBe("Input should be a valid integer; Field required");
  });

  it("never returns an HTML proxy page as the message", () => {
    const html = "<html><body><h1>502 Bad Gateway</h1></body></html>";
    const msg = formatApiError(new ApiError(502, html));
    expect(msg).not.toContain("<");
    expect(msg).toBe(i18n.t("errors.server_5xx"));
  });

  it("falls back to a localized message by status when the body carries no usable detail", () => {
    expect(formatApiError(new ApiError(429, ""))).toBe(i18n.t("errors.rate_limited"));
    expect(formatApiError(new ApiError(404, "{}"))).toBe(i18n.t("errors.not_found"));
    expect(formatApiError(new ApiError(400, '{"detail":{"a":1}}'))).toBe(i18n.t("errors.generic_status", { status: 400 }));
  });

  it("keeps a non-API error's own message", () => {
    expect(formatApiError(new Error("boom"))).toBe("boom");
  });
});
