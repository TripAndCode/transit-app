import i18n from "../i18n";

const BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`API ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

/** GET. Pass react-query's `signal` so in-flight requests are aborted when
 * the query key changes or the consuming component unmounts — without it,
 * rapid filter changes leave orphaned fetches racing each other. */
export async function apiGet<T>(path: string, opts?: { signal?: AbortSignal }): Promise<T> {
  return request<T>(path, { method: "GET", signal: opts?.signal });
}

/** GET that returns null on 401. Used for the anonymous-allowed `/api/me` probe. */
export async function apiGetOrNull<T>(path: string): Promise<T | null> {
  try {
    return await request<T>(path, { method: "GET" });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

/** POST — tolerates 204 No Content (returns undefined when the endpoint
 * intentionally has no JSON body, e.g. logout). The signature stays
 * `Promise<T>` so JSON-returning callers (`/ask`, `/agencies`,
 * `/admin/users/:uid` PATCH) don't have to narrow — callers of
 * 204-only endpoints should type T as `void`. */
export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return requestMaybeEmpty<T>(path, { method: "POST", body: JSON.stringify(body) }) as Promise<T>;
}

/** PATCH — same JSON-or-204 contract as apiPost. */
export async function apiPatch<T>(path: string, body: unknown): Promise<T> {
  return requestMaybeEmpty<T>(path, { method: "PATCH", body: JSON.stringify(body) }) as Promise<T>;
}

/** DELETE — handles 204 No Content (returns undefined when no JSON body). */
export async function apiDelete<T = void>(path: string): Promise<T | undefined> {
  return requestMaybeEmpty<T>(path, { method: "DELETE" });
}

/** Extract a human-readable message from an unknown error, preferring an
 * `ApiError`'s parsed `detail` field. Mirrors the previous per-caller
 * `Error(detail.detail ?? detail)` shape so existing UI text stays intact. */
export function formatApiError(e: unknown): string {
  if (e instanceof ApiError) {
    try {
      const parsed = JSON.parse(e.body);
      if (parsed && typeof parsed === "object" && typeof parsed.detail === "string") {
        return parsed.detail;
      }
    } catch {
      // body wasn't JSON — fall through
    }
    return e.body || e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const r = await rawFetch(path, init);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(r.status, text);
  }
  try {
    return (await r.json()) as T;
  } catch {
    throw new ApiError(r.status, "Response was not valid JSON");
  }
}

async function requestMaybeEmpty<T>(path: string, init: RequestInit): Promise<T | undefined> {
  const r = await rawFetch(path, init);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(r.status, text);
  }
  if (r.status === 204) return undefined;
  try {
    return (await r.json()) as T;
  } catch {
    throw new ApiError(r.status, "Response was not valid JSON");
  }
}

// credentials:'include' so cross-origin Vite-dev (:5173 → :8000) sends the sid
// cookie. Same-origin requests (single-origin prod / make serve) are
// unaffected — browsers always send same-origin cookies.
//
// Accept-Language is stamped from the current i18n instance so the
// backend (Ask LLM prelude / formatter / tool summaries) renders in
// the same language the user picked in the UI. Falls back to "ja" so
// the header is always present, matching the LocaleMiddleware default.
async function rawFetch(path: string, init: RequestInit): Promise<Response> {
  const apiKey = localStorage.getItem("api_key");
  const lang = i18n.resolvedLanguage ?? i18n.language ?? "ja";
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept-Language": lang,
    ...(apiKey ? { "X-API-Key": apiKey } : {}),
  };
  return fetch(`${BASE}${path}`, { ...init, headers, credentials: "include" });
}
