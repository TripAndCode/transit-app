// @vitest-environment node
import { describe, it, expect, vi, afterEach } from "vitest";
import { ApiError, apiGet, onApiKeyRejected, saveApiKey, storedApiKey } from "./client";

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

  it("is reported once when several requests carrying it are rejected together", async () => {
    vi.stubGlobal("localStorage", memoryStorage({ api_key: "stale" }));
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init: RequestInit & { headers: Record<string, string> }) =>
        Promise.resolve(init.headers["X-API-Key"] ? json(401, { detail: "Invalid API key" }) : json(200, {})),
      ),
    );
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await Promise.all([apiGet("/api/config"), apiGet("/api/me"), apiGet("/api/agencies")]);

    expect(rejected).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("leaves a key saved while the rejected request was in flight", async () => {
    const storage = memoryStorage({ api_key: "stale" });
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementationOnce(() => {
          storage.store.set("api_key", "fresh");
          return Promise.resolve(json(401, { detail: "Invalid API key" }));
        })
        .mockResolvedValueOnce(json(200, {})),
    );
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await apiGet("/api/config");

    expect(storage.store.get("api_key")).toBe("fresh");
    expect(sentKey((fetch as ReturnType<typeof vi.fn>).mock.calls[1])).toBe("fresh");
    expect(rejected).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("is sent trimmed and kept when it was stored with whitespace around it", async () => {
    const storage = memoryStorage({ api_key: " pro-key \n" });
    vi.stubGlobal("localStorage", storage);
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await apiGet("/api/config");

    expect(sentKey(fetchMock.mock.calls[0])).toBe("pro-key");
    expect(storage.store.get("api_key")).toBe("pro-key");
    expect(rejected).not.toHaveBeenCalled();
    unsubscribe();
  });

  it("drops a whitespace-padded key the server rejects", async () => {
    const storage = memoryStorage({ api_key: "stale " });
    vi.stubGlobal("localStorage", storage);
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(json(401, { detail: "Invalid API key" }))
        .mockResolvedValueOnce(json(200, {})),
    );
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await apiGet("/api/config");

    expect(storage.store.has("api_key")).toBe(false);
    expect(rejected).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("bounds the retry by the same timeout as the first attempt", async () => {
    vi.stubGlobal("localStorage", memoryStorage({ api_key: "stale" }));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(401, { detail: "Invalid API key" }))
      .mockResolvedValueOnce(json(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    // Subscribed so the rejection is not held over for a later test's listener.
    const unsubscribe = onApiKeyRejected(() => {});

    await apiGet("/api/config");

    const [first, second] = fetchMock.mock.calls.map((c) => (c[1] as RequestInit).signal);
    expect(second).toBe(first);
    unsubscribe();
  });

  it("is dropped before sending when it cannot travel in a header at all", async () => {
    // A kana from an IME: fetch would reject the header outright.
    const storage = memoryStorage({ api_key: "keyあ" });
    vi.stubGlobal("localStorage", storage);
    const fetchMock = vi.fn().mockResolvedValue(json(200, { ok: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const rejected = vi.fn();
    const unsubscribe = onApiKeyRejected(rejected);

    await expect(apiGet("/api/config")).resolves.toEqual({ ok: 1 });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(sentKey(fetchMock.mock.calls[0])).toBeUndefined();
    expect(storage.store.has("api_key")).toBe(false);
    expect(rejected).toHaveBeenCalledTimes(1);
    unsubscribe();
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

describe("saveApiKey", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("stores the trimmed key, and clears it for an empty one", () => {
    const storage = memoryStorage();
    vi.stubGlobal("localStorage", storage);
    saveApiKey("  pro-key \n");
    expect(storedApiKey()).toBe("pro-key");
    saveApiKey("   ");
    expect(storage.store.has("api_key")).toBe(false);
  });
});

describe("blocked web storage", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads no key and saves nothing, without throwing", () => {
    const blocked = () => {
      throw new DOMException("blocked", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: blocked, setItem: blocked, removeItem: blocked });
    expect(storedApiKey()).toBeNull();
    expect(() => saveApiKey("k")).not.toThrow();
  });

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
