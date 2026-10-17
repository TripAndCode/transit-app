// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, apiGet, onApiKeyRejected } from "./client";

function memoryStorage(initial: Record<string, string> = {}) {
  const store = new Map(Object.entries(initial));
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    store,
  };
}

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function sentKey(call: unknown[]): string | undefined {
  return (call[1] as RequestInit & { headers: Record<string, string> }).headers["X-API-Key"];
}

describe("a stored API key the server rejects", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is dropped and the request retried once without it", async () => {
    const storage = memoryStorage({ api_key: "stale" });
    vi.stubGlobal("localStorage", storage);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(401, { detail: "Invalid API key" }))
      .mockResolvedValueOnce(json(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await expect(apiGet("/api/config")).resolves.toEqual({ ok: 1 });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(sentKey(fetchMock.mock.calls[0])).toBe("stale");
    expect(sentKey(fetchMock.mock.calls[1])).toBeUndefined();
    expect(storage.store.has("api_key")).toBe(false);
    expect(rejected).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("is reported to a listener that subscribes only after the rejection", async () => {
    vi.stubGlobal("localStorage", memoryStorage({ api_key: "stale" }));
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(401, { detail: "Invalid API key" }))
        .mockResolvedValueOnce(json(200, {})),
    );
    await apiGet("/api/config");

    const late = vi.fn();
    const unsubscribe = onApiKeyRejected(late);
    expect(late).toHaveBeenCalledTimes(1);
    unsubscribe();

    const later = vi.fn();
    onApiKeyRejected(later)();
    expect(later).not.toHaveBeenCalled();
  });

  it("keeps the key and does not retry on any other 401", async () => {
    const storage = memoryStorage({ api_key: "good" });
    vi.stubGlobal("localStorage", storage);
    const fetchMock = vi.fn().mockResolvedValue(json(401, { detail: "auth required" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/me")).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(storage.store.get("api_key")).toBe("good");
  });

  it("does not retry a rejection the request carried no key for", async () => {
    vi.stubGlobal("localStorage", memoryStorage());
    const fetchMock = vi.fn().mockResolvedValue(json(401, { detail: "Invalid API key" }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/config")).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe("blocked web storage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("still sends the request, without a key", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new DOMException("blocked", "SecurityError");
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiGet("/api/config")).resolves.toEqual({ ok: 1 });
    expect(sentKey(fetchMock.mock.calls[0])).toBeUndefined();
  });
});
