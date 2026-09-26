import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";
import type { Agency } from "./types";
import { useAgencies } from "./hooks";
import { DEFAULT_RANGE_DAYS, isoDaysAgo, isoDaysBefore } from "./rangeContext";

/**
 * Pure decision: on a fresh visit (no explicit from/to already in `params`),
 * would the default 30-day window need anchoring at the agency's real
 * latest data date instead of today, because today's naive default window
 * would otherwise be guaranteed empty? Returns `null` when no rewrite is
 * needed (explicit range already present, agency has no data at all, or the
 * naive window already covers real data).
 *
 * Exported so `useAnonymousFilterPersistence` can derive the identical
 * answer from the identical (`agencyId`, `agencies`, `params`) inputs on the
 * same render, and defer to it — see that hook's docstring for why this has
 * to be a plain pure function shared between the two hooks rather than a
 * `useState`-based "has the anchor settled yet" flag: both hooks' effects
 * fire from the same commit, so a stateful readiness flag toggled by a
 * `setState` call inside this hook's own effect couldn't take effect until
 * a LATER render anyway (this repo also lints against exactly that
 * synchronization-effect pattern, react-hooks/set-state-in-effect) — a pure
 * function both hooks can call independently at render time needs no such
 * cross-render delay.
 */
export function computeAnchorRange(
  agencyId: number | null,
  agencies: Agency[] | undefined,
  params: URLSearchParams,
): { from: string; to: string } | null {
  if (agencyId == null || !agencies) return null;
  if (params.get("from") || params.get("to")) return null;

  const agency = agencies.find((a) => a.agency_id === agencyId);
  const latestDataDate = agency?.latest_data_date;
  if (!latestDataDate) return null;

  const windowStart = isoDaysAgo(DEFAULT_RANGE_DAYS - 1);
  if (latestDataDate >= windowStart) return null; // already inside today's default window

  return { from: isoDaysBefore(latestDataDate, DEFAULT_RANGE_DAYS - 1), to: latestDataDate };
}

/**
 * The agency's most recent `DEFAULT_RANGE_DAYS`-day window, ending at its
 * real latest data date — regardless of whether today's naive window would
 * already cover it. Unlike {@link computeAnchorRange}, this is not a "does
 * this rewrite need to happen" decision for a fresh visit; it is the target
 * for a manual "take me to real data" recovery a user taps from an
 * EmptyState after a route/service filter (not the date range itself) left
 * the view empty, so it always returns the window when the agency has any
 * data at all.
 */
export function latestDataWindow(
  agencyId: number | null,
  agencies: Agency[] | undefined,
): { from: string; to: string } | null {
  if (agencyId == null || !agencies) return null;
  const latestDataDate = agencies.find((a) => a.agency_id === agencyId)?.latest_data_date;
  if (!latestDataDate) return null;
  return { from: isoDaysBefore(latestDataDate, DEFAULT_RANGE_DAYS - 1), to: latestDataDate };
}

/**
 * A recovery callback that overwrites the URL's from/to with the agency's
 * latest-data window, leaving every other query param untouched. Returns
 * `null` (render no control) when the agency has no data to jump to.
 */
export function useJumpToLatestDataRange(agencyId: number | null): (() => void) | null {
  const { data: agencies } = useAgencies();
  const [, setParams] = useSearchParams();
  const range = latestDataWindow(agencyId, agencies);
  if (!range) return null;
  return () => {
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("from", range.from);
        next.set("to", range.to);
        return next;
      },
      { replace: true },
    );
  };
}

/**
 * On a fresh visit (no explicit from/to in the URL), rewrites the URL to
 * anchor the default 30-day window at the agency's real latest data date
 * instead of today, when today's default window would otherwise be
 * guaranteed empty. Never touches an explicit from/to already present, and
 * never fires for an agency with no aggregated data at all (its EmptyState
 * is already correct in that case).
 *
 * Reads the already-cached agencies list (useAgencies, 5min staleTime,
 * already fetched by AgencyPicker on nearly every page) — no new network
 * round-trip in the common case.
 */
export function useDefaultRangeAnchor(agencyId: number | null): void {
  const { data: agencies } = useAgencies();
  const [params, setParams] = useSearchParams();

  useEffect(() => {
    const range = computeAnchorRange(agencyId, agencies, params);
    if (!range) return;

    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.set("from", range.from);
        next.set("to", range.to);
        return next;
      },
      { replace: true },
    );
  }, [agencyId, agencies, params, setParams]);
}
