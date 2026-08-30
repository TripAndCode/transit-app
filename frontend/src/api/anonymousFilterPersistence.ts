import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useSession } from "./auth";
import type { DowFilter, ServiceFilter, TimeBand } from "./rangeContext";

type StoredFilter = {
  from?: string;
  to?: string;
  dow?: DowFilter;
  time_band?: TimeBand;
  service?: ServiceFilter;
  routes?: string[];
};

// Single source of truth for the scalar filter param names shared by
// hasAnyFilterParam/restore/persist below — `routes` is handled separately
// everywhere since it's array-valued (comma-joined in the URL) rather than
// a plain string.
const SCALAR_FILTER_KEYS = ["from", "to", "dow", "time_band", "service"] as const;

const KEY_PREFIX = "transit.lastFilter.";

function storageKey(agencyId: number): string {
  return `${KEY_PREFIX}${agencyId}`;
}

function readStored(agencyId: number): StoredFilter | null {
  try {
    const raw = localStorage.getItem(storageKey(agencyId));
    if (!raw) return null;
    return JSON.parse(raw) as StoredFilter;
  } catch {
    return null;
  }
}

function writeStored(agencyId: number, value: StoredFilter): void {
  try {
    localStorage.setItem(storageKey(agencyId), JSON.stringify(value));
  } catch {
    /* localStorage unavailable (private browsing, quota) — fail open, same
     * as Sidebar.tsx's readCollapsedPref/writeCollapsedPref. */
  }
}

/**
 * Anonymous-only convenience: remember the last-used date range/DOW/
 * time-band/routes filter per agency in localStorage, and restore it on a
 * fresh visit that has no explicit filter params in the URL. This targets a
 * distinct, smaller friction than PresetMenu's login-gated named presets
 * (`/api/me/presets`): a single "remember what I was just looking at" slot
 * rather than durable, named, multi-slot filter sets. GuestPrompt's "sign in
 * to save your filters" nudge still points at the stronger presets feature —
 * this only removes the friction of every anonymous page load starting from
 * a blank filter state, it doesn't let an anonymous user save/name/switch
 * between multiple filter sets the way signing in does.
 *
 * Logged-in users are intentionally unaffected (this hook no-ops once
 * `session` is present) — their filters already benefit from the explicit,
 * durable presets feature instead.
 */
export function useAnonymousFilterPersistence(agencyId: number | null): void {
  const { data: session, isLoading } = useSession();
  const [params, setParams] = useSearchParams();
  // Tracks which agency we've already attempted a restore for, so an
  // explicit in-session reset (which clears every filter param) doesn't
  // immediately get overwritten by a re-restore of the old stored value.
  const restoredFor = useRef<number | null>(null);

  useEffect(() => {
    if (isLoading || session || agencyId == null) return;

    const hasAnyFilterParam = Boolean(
      SCALAR_FILTER_KEYS.some((key) => params.get(key)) || params.get("routes"),
    );

    if (!hasAnyFilterParam && restoredFor.current !== agencyId) {
      restoredFor.current = agencyId;
      const stored = readStored(agencyId);
      // An all-undefined `{}` can legitimately be what a filterless visit
      // persisted (see below) — only treat it as restorable if it actually
      // has a value to restore, otherwise this branch would fire a no-op
      // `setParams` on every subsequent filterless visit.
      if (stored && Object.keys(stored).length > 0) {
        setParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            for (const key of SCALAR_FILTER_KEYS) {
              const value = stored[key];
              if (value) next.set(key, value);
            }
            if (stored.routes && stored.routes.length > 0) next.set("routes", stored.routes.join(","));
            return next;
          },
          { replace: true },
        );
        return;
      }
    }

    // Persist whatever's currently in the URL, including an explicitly
    // cleared state — a later visit should remember the most recent choice,
    // not stubbornly reapply the first one ever made.
    restoredFor.current = agencyId;
    const routesStr = params.get("routes");
    const nextStored: StoredFilter = {};
    for (const key of SCALAR_FILTER_KEYS) {
      const value = params.get(key);
      if (value) (nextStored as Record<string, string>)[key] = value;
    }
    if (routesStr) {
      const routes = routesStr.split(",").filter(Boolean);
      if (routes.length > 0) nextStored.routes = routes;
    }
    writeStored(agencyId, nextStored);
    // `params` (not a derived string key) is the dependency, matching
    // useDefaultRangeAnchor's pattern: react-router memoizes useSearchParams'
    // return value on `location.search`, so this only re-runs when the URL's
    // query string actually changes, not on every unrelated render.
  }, [agencyId, isLoading, session, params, setParams]);
}
