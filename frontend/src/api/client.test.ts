// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import i18n from "../i18n";
import { ApiError, REQUEST_TIMEOUT_MS, apiGet, apiPatch, formatApiError, isAggregateNotReady } from "./client";
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

describe("without AbortSignal.any", () => {
  // Safari before 17.4, Chrome before 116 and Firefox before 124 have no
  // AbortSignal.any; combining the caller's signal with the timeout must
  // still work there rather than throw a TypeError on every request.
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  function withoutAny() {
    const original = Object.getOwnPropertyDescriptor(AbortSignal, "any");
    Object.defineProperty(AbortSignal, "any", { value: undefined, configurable: true, writable: true });
    return () => {
      if (original) Object.defineProperty(AbortSignal, "any", original);
      else delete (AbortSignal as unknown as { any?: unknown }).any;
    };
  }

  it("still sends a request that carries a caller signal, and the caller's abort reaches fetch", async () => {
    const restore = withoutAny();
    try {
      const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
      vi.stubGlobal("fetch", fetchMock);
      vi.stubGlobal("localStorage", { getItem: () => null });
      const controller = new AbortController();

      await apiPatch("/api/admin/users/1", { role: "admin" }, { signal: controller.signal });

      const sent = fetchMock.mock.calls[0]?.[1]?.signal as AbortSignal;
      expect(sent.aborted).toBe(false);
      const reason = new DOMException("Aborted by the caller.", "AbortError");
      controller.abort(reason);
      expect(sent.aborted).toBe(true);
      expect(sent.reason).toBe(reason);
    } finally {
      restore();
    }
  });

  it("still fails a stalled request with the timeout's own TimeoutError", async () => {
    const restore = withoutAny();
    try {
      const deadline = new AbortController();
      vi.spyOn(AbortSignal, "timeout").mockReturnValue(deadline.signal);
      vi.stubGlobal(
        "fetch",
        vi.fn(
          (_url: string, init: RequestInit) =>
            new Promise<Response>((_resolve, reject) => {
              init.signal?.addEventListener("abort", () => reject(init.signal?.reason));
            }),
        ),
      );
      vi.stubGlobal("localStorage", { getItem: () => null });

      const settled = apiGet("/api/slow", { signal: new AbortController().signal }).then(
        () => null,
        (e: unknown) => e,
      );
      deadline.abort(new DOMException("The operation timed out.", "TimeoutError"));

      const err = await settled;
      expect((err as Error).name).toBe("TimeoutError");
      expect(classifyError(err)).toBe("timeout");
    } finally {
      restore();
    }
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
