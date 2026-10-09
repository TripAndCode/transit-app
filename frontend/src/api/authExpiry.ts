import type { QueryClient } from "@tanstack/react-query";
import { ApiError, apiErrorDetail } from "./client";

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

/** One retry for a failed query, none for an auth-required 401: repeating it
 *  cannot succeed until the visitor signs in. */
export function retryUnlessAuthRequired(failureCount: number, err: unknown): boolean {
  return !isAuthRequired(err) && failureCount < 1;
}
