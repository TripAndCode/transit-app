import { useEffect, useEffectEvent, useState } from "react";

/** Backoff schedule for the two quiet attempts before handing off to a
 *  manual retry button. */
export const AUTO_RETRY_DELAYS_MS = [1500, 4000];

/**
 * Drives `AsyncSection`'s quiet auto-retry: while `hasError` and
 * `transient` both hold and a retry callback is available, silently calls
 * `onRetry` on the backoff schedule above (never announcing the failure to
 * the user), then gives up after both attempts so the caller can fall back
 * to a visible error with a manual retry.
 *
 * The attempt count resets the moment `hasError` goes false (the section
 * loaded successfully), so a later, unrelated failure gets its own two
 * quiet attempts rather than inheriting an exhausted counter from the last
 * one.
 */
export function useAutoRetry(
  hasError: boolean,
  transient: boolean,
  onRetry: (() => void) | undefined,
): { retrying: boolean } {
  const [attempt, setAttempt] = useState(0);
  const [wasError, setWasError] = useState(hasError);
  if (hasError !== wasError) {
    setWasError(hasError);
    if (!hasError) setAttempt(0);
  }

  const hasRetry = onRetry != null;
  const exhausted = attempt >= AUTO_RETRY_DELAYS_MS.length;
  const active = hasError && transient && hasRetry && !exhausted;

  // useEffectEvent so a parent re-render that hands down a structurally new
  // `onRetry` closure (e.g. an inline `() => query.refetch()`) can't restart
  // this effect and push the scheduled retry back out — only `attempt`
  // changing (a real completed wait) should reschedule.
  const fireRetry = useEffectEvent(() => {
    onRetry?.();
  });

  useEffect(() => {
    if (!active) return;
    const id = setTimeout(() => {
      setAttempt((a) => a + 1);
      fireRetry();
    }, AUTO_RETRY_DELAYS_MS[attempt]);
    return () => clearTimeout(id);
  }, [active, attempt]);

  return { retrying: active };
}
