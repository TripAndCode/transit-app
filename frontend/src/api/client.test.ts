// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, REQUEST_TIMEOUT_MS, apiGet, apiPatch, isAggregateNotReady } from "./client";
import { classifyError } from "./errorClass";

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

    // The request signal is the caller's combined with the client's timeout,
    // so it is not the same object; aborting the caller's must abort it.
    const sent = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal;
    expect(sent.aborted).toBe(false);
    controller.abort();
    expect(sent.aborted).toBe(true);
  });
});

describe("request timeout", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  /** A fetch that never answers and rejects the way fetch does on abort. */
  function hangingFetch() {
    return vi.fn((_url: string, init: RequestInit) => {
      return new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
      });
    });
  }

  it("fails a stalled request with a TimeoutError that classifies as timeout", async () => {
    // AbortSignal.timeout runs on the runtime's own timer, which fake timers
    // do not drive, so the test owns the signal it hands out.
    const deadline = new AbortController();
    const timeoutSpy = vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
    vi.stubGlobal("fetch", hangingFetch());
    vi.stubGlobal("localStorage", { getItem: () => null });

    const settled = apiGet("/api/slow").then(
      () => null,
      (e: unknown) => e,
    );
    deadline.abort(new DOMException("The operation timed out.", "TimeoutError"));

    const err = await settled;
    expect(timeoutSpy).toHaveBeenCalledWith(REQUEST_TIMEOUT_MS);
    expect((err as Error).name).toBe("TimeoutError");
    expect(classifyError(err)).toBe("timeout");
  });

  it("classifies a stall during the response body as timeout, not invalid JSON", async () => {
    const deadline = new AbortController();
    vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
    // Headers arrive; the body then stalls until the signal aborts.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => ({
        ok: true,
        status: 200,
        json: () =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
          }),
      })),
    );
    vi.stubGlobal("localStorage", { getItem: () => null });

    const settled = apiGet("/api/slow-body").then(
      () => null,
      (e: unknown) => e,
    );
    await new Promise((r) => setTimeout(r, 0));
    deadline.abort(new DOMException("The operation timed out.", "TimeoutError"));

    const err = await settled;
    expect((err as Error).name).toBe("TimeoutError");
    expect(classifyError(err)).toBe("timeout");
  });

  it("still lets the caller abort a request before the timeout", async () => {
    vi.stubGlobal("fetch", hangingFetch());
    vi.stubGlobal("localStorage", { getItem: () => null });
    const controller = new AbortController();

    const settled = apiGet("/api/slow", { signal: controller.signal }).then(
      () => null,
      (e: unknown) => e,
    );
    controller.abort();

    const err = await settled;
    expect((err as Error).name).toBe("AbortError");
    expect(classifyError(err)).not.toBe("timeout");
  });
});
