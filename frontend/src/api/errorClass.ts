import { ApiError, isAggregateNotReady, isLlmNotApproved } from "./client";

/**
 * The error classes `ErrorBanner` gives distinct copy and a distinct
 * recovery to. `network`/`timeout` are the transient pair `AsyncSection`
 * quietly auto-retries before ever showing a banner; every other class is
 * either a standing condition (no retry helps) or a real failure the user
 * should decide whether to retry.
 */
export type ErrorClass =
  | "network"
  | "timeout"
  | "not_ready"
  | "not_approved"
  | "not_found"
  | "rate_limited"
  | "server"
  | "generic";

function isTimeoutError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "TimeoutError") ||
    (err instanceof Error && err.name === "TimeoutError")
  );
}

/** Classify an unknown error thrown by the API client. Order matters: the
 *  two standing-condition checks run before the generic ApiError status
 *  branches so a 403/503 that also happens to carry one of those specific
 *  machine-readable codes is never mistaken for a plain permission/server
 *  error. */
export function classifyError(err: unknown): ErrorClass {
  if (isLlmNotApproved(err)) return "not_approved";
  if (isAggregateNotReady(err)) return "not_ready";
  if (err instanceof ApiError) {
    if (err.status === 429) return "rate_limited";
    if (err.status === 404) return "not_found";
    if (err.status >= 500) return "server";
    return "generic";
  }
  if (isTimeoutError(err)) return "timeout";
  // Everything fetch() itself can throw (network drop, CORS, DNS) surfaces
  // as a plain Error/TypeError, never an ApiError (which only wraps HTTP-
  // level failures) -- so any other Error reaching here is transport-level.
  if (err instanceof Error) return "network";
  return "generic";
}

/** True for the two classes worth a quiet, unannounced retry (a blip, not a
 *  standing condition) -- see `AsyncSection`'s auto-retry. */
export function isTransientErrorClass(cls: ErrorClass): boolean {
  return cls === "network" || cls === "timeout";
}
