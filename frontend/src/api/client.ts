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

/** Machine code the API returns (503) when a read endpoint's precomputed
 * aggregate table doesn't exist yet on this deployment (migrations/analyze
 * behind). Mirrors api/aggregate_errors.py::AGGREGATE_NOT_READY_CODE. */
const AGGREGATE_NOT_READY_CODE = "aggregate_not_ready";

/** True when an error is the "aggregates not built in this environment" 503 —
 * a persistent, not-transient condition, so the UI should explain it calmly and
 * NOT offer a futile retry. */
export function isAggregateNotReady(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 503) return false;
  try {
    const parsed = JSON.parse(err.body);
    return parsed?.code === AGGREGATE_NOT_READY_CODE;
  } catch {
    return false;
  }
}

/** Detail string the API returns (403) on /followup when
 * the caller isn't an admin-approved signed-in user — anonymous callers 403
 * here too. Mirrors api/security.py::require_llm_approved. */
const LLM_NOT_APPROVED_DETAIL = "llm_not_approved";

/** True when an error is the admin-approval-required 403 — a standing
 * condition until an admin flips the flag, not a transient failure, so the
 * UI should explain it calmly and never offer a retry. */
export function isLlmNotApproved(err: unknown): boolean {
  if (!(err instanceof ApiError) || err.status !== 403) return false;
  return apiErrorDetail(err) === LLM_NOT_APPROVED_DETAIL;
}

/** GET. Pass react-query's `signal` so in-flight requests are aborted when
 * the query key changes or the consuming component unmounts — without it,
 * rapid filter changes leave orphaned fetches racing each other. */
export async function apiGet<T>(path: string, opts?: { signal?: AbortSignal }): Promise<T> {
  return request<T>(path, { method: "GET", signal: opts?.signal });
}

/** GET that returns null on 401. Used for the anonymous-allowed `/api/me` probe. */
export async function apiGetOrNull<T>(path: string, opts?: { signal?: AbortSignal }): Promise<T | null> {
  try {
    return await request<T>(path, { method: "GET", signal: opts?.signal });
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) return null;
    throw e;
  }
}

/** POST — tolerates 204 No Content (returns undefined when the endpoint
 * intentionally has no JSON body, e.g. logout). The signature stays
 * `Promise<T>` so JSON-returning callers (`/ask`, `/agencies`,
 * `/admin/users/:uid` PATCH) don't have to narrow — callers of
 * 204-only endpoints should type T as `void`. Accepts an optional `signal`
 * (mirroring `apiGet`) so a react-query `queryFn` can abort a superseded,
 * still-in-flight POST instead of letting it run to completion unseen, as the
 * Copilot insight query does. */
export async function apiPost<T>(path: string, body: unknown, opts?: { signal?: AbortSignal }): Promise<T> {
  return requestMaybeEmpty<T>(path, {
    method: "POST",
    body: JSON.stringify(body),
    signal: opts?.signal,
  }) as Promise<T>;
}

/** PATCH — same JSON-or-204 contract as apiPost. */
export async function apiPatch<T>(path: string, body: unknown, opts?: { signal?: AbortSignal }): Promise<T> {
  return requestMaybeEmpty<T>(path, {
    method: "PATCH",
    body: JSON.stringify(body),
    signal: opts?.signal,
  }) as Promise<T>;
}

/** PUT — same JSON-or-204 contract as apiPost. Used for idempotent
 * replace-the-whole-resource endpoints, e.g. `/api/me/llm-key`. */
export async function apiPut<T>(path: string, body: unknown, opts?: { signal?: AbortSignal }): Promise<T> {
  return requestMaybeEmpty<T>(path, {
    method: "PUT",
    body: JSON.stringify(body),
    signal: opts?.signal,
  }) as Promise<T>;
}

/** DELETE — handles 204 No Content (returns undefined when no JSON body). ``body``,
 *  when given, is sent as JSON (e.g. a typed confirmation). */
export async function apiDelete<T = void>(
  path: string,
  opts?: { signal?: AbortSignal; body?: unknown },
): Promise<T | undefined> {
  return requestMaybeEmpty<T>(path, {
    method: "DELETE",
    body: opts?.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts?.signal,
  });
}

/** Parsed `detail` field of an `ApiError`'s JSON body, e.g. FastAPI's
 * `HTTPException(detail=...)`. `null` if `err` isn't an `ApiError` or its
 * body isn't `{"detail": string}`. */
export function apiErrorDetail(err: unknown): string | null {
  if (!(err instanceof ApiError)) return null;
  try {
    const parsed = JSON.parse(err.body);
    return parsed && typeof parsed === "object" && typeof parsed.detail === "string"
      ? parsed.detail
      : null;
  } catch {
    return null;
  }
}

/** The `msg` fields of a FastAPI validation error (`detail` is a list of
 * `{msg, ...}` objects), joined; `null` when the body is not that shape. */
function validationMessages(err: ApiError): string | null {
  try {
    const parsed = JSON.parse(err.body);
    if (!Array.isArray(parsed?.detail)) return null;
    const msgs = parsed.detail
      .map((d: unknown) => (d && typeof d === "object" ? (d as { msg?: unknown }).msg : undefined))
      .filter((m: unknown): m is string => typeof m === "string" && m !== "");
    return msgs.length > 0 ? msgs.join("; ") : null;
  } catch {
    return null;
  }
}

/** Extract a human-readable message from an unknown error, preferring an
 * `ApiError`'s parsed `detail` field. Mirrors the previous per-caller
 * `Error(detail.detail ?? detail)` shape so existing UI text stays intact. */
export function formatApiError(e: unknown): string {
  if (e instanceof ApiError) {
    const detail = apiErrorDetail(e);
    if (detail != null) return detail;
    const validation = validationMessages(e);
    if (validation != null) return validation;
    // The body is not a `{detail: string}` the server wrote for people: it may
    // be a proxy's HTML page or a machine-readable payload, so it is never
    // shown. A 2xx/3xx ApiError carries the client's own message instead.
    if (e.status < 400) return e.body || e.message;
    if (e.status === 429) return i18n.t("errors.rate_limited");
    if (e.status === 404) return i18n.t("errors.not_found");
    if (e.status >= 500) return i18n.t("errors.server_5xx");
    return i18n.t("errors.generic_status", { status: e.status });
  }
  return e instanceof Error ? e.message : String(e);
}

/** Parse a response body as JSON. A timeout or caller abort that lands while
 *  the body is still streaming is rethrown as-is: folding it into "not valid
 *  JSON" would hide a stall behind a generic error class. */
async function parseJsonBody<T>(r: Response): Promise<T> {
  try {
    return (await r.json()) as T;
  } catch (e) {
    if (e instanceof Error && (e.name === "TimeoutError" || e.name === "AbortError")) throw e;
    throw new ApiError(r.status, "Response was not valid JSON");
  }
}

async function request<T>(path: string, init: RequestInit): Promise<T> {
  const r = await rawFetch(path, init);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(r.status, text);
  }
  return parseJsonBody<T>(r);
}

async function requestMaybeEmpty<T>(path: string, init: RequestInit): Promise<T | undefined> {
  const r = await rawFetch(path, init);
  if (!r.ok) {
    const text = await r.text().catch(() => "");
    throw new ApiError(r.status, text);
  }
  if (r.status === 204) return undefined;
  return parseJsonBody<T>(r);
}

/** Upper bound on one request, headers and body together. A hung backend then
 *  surfaces as a `TimeoutError` (classified `timeout`) instead of a section
 *  that stays on its skeleton until the browser gives up. It sits above the
 *  server's own limits (30s statement timeout, 30s per LLM call) so a request
 *  the server would still answer is not cut off. */
export const REQUEST_TIMEOUT_MS = 90_000;

/** Aborts as soon as any of `signals` does, with that signal's own reason, so a
 *  caller's `AbortError` and the timeout's `TimeoutError` stay distinguishable.
 *  `AbortSignal.any` is used where it exists (Safari 17.4, Chrome 116,
 *  Firefox 124); older browsers get the equivalent wiring by hand. */
function anySignal(signals: AbortSignal[]): AbortSignal {
  if (typeof AbortSignal.any === "function") return AbortSignal.any(signals);
  const controller = new AbortController();
  const settled = signals.find((s) => s.aborted);
  if (settled) {
    controller.abort(settled.reason);
    return controller.signal;
  }
  const onAbort = (e: Event) => {
    for (const s of signals) s.removeEventListener("abort", onAbort);
    controller.abort((e.target as AbortSignal).reason);
  };
  for (const s of signals) s.addEventListener("abort", onAbort);
  return controller.signal;
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
  const timeout = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  const signal = init.signal ? anySignal([init.signal, timeout]) : timeout;
  return fetch(`${BASE}${path}`, { ...init, headers, credentials: "include", signal });
}
