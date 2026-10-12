import type { QueryClient } from "@tanstack/react-query";
import { ApiError, apiErrorDetail } from "./client";
import { classifyError } from "./errorClass";

/** Detail the API returns with a 401 when a request needs a signed-in caller.
 *  Mirrors api/security.py::require_user and the login gate. */
const AUTH_REQUIRED_DETAIL = "auth required";

export function isAuthRequired(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401 && apiErrorDetail(err) === AUTH_REQUIRED_DETAIL;
}

/** Refetches the session and the client config after the API refuses a
 *  request for want of a session, so `RequireAuth` decides again on fresh
 *  values. The config is refetched too because it is cached forever: a copy
 *  loaded before the API required sign-in would otherwise keep saying it
 *  does not. */
export function refreshAuthStateOn401(queryClient: QueryClient): (err: unknown) => void {
  return (err) => {
    if (!isAuthRequired(err)) return;
    void queryClient.invalidateQueries({ queryKey: ["me"] });
    void queryClient.invalidateQueries({ queryKey: ["config"] });
  };
}

/** One retry for a failed query, and only when repeating it can plausibly
 *  succeed: a dropped connection or a 5xx. Never for a 4xx (the same request
 *  is refused again), a 429 (a retry spends more of the rate-limit budget),
 *  an auth-required 401, or the standing conditions (not approved, aggregates
 *  not built), which `classifyError` sorts out of the "server" class. A
 *  timeout is not retried either: the request has already waited the full
 *  limit, and a second wait doubles the time to any message. */
export function retryTransientOnce(failureCount: number, err: unknown): boolean {
  if (failureCount >= 1 || isAuthRequired(err)) return false;
  const cls = classifyError(err);
  return cls === "network" || cls === "server";
}
