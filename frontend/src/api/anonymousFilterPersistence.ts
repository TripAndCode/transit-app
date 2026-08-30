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
      params.get("from") ||
        params.get("to") ||
        params.get("dow") ||
        params.get("time_band") ||
        params.get("service") ||
        params.get("routes"),
    );

    if (!hasAnyFilterParam && restoredFor.current !== agencyId) {
      restoredFor.current = agencyId;
      const stored = readStored(agencyId);
      if (stored) {
        setParams(
          (prev) => {
            const next = new URLSearchParams(prev);
            if (stored.from) next.set("from", stored.from);
            if (stored.to) next.set("to", stored.to);
            if (stored.dow) next.set("dow", stored.dow);
            if (stored.time_band) next.set("time_band", stored.time_band);
            if (stored.service) next.set("service", stored.service);
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
    writeStored(agencyId, {
      from: params.get("from") ?? undefined,
      to: params.get("to") ?? undefined,
      dow: (params.get("dow") as DowFilter) || undefined,
      time_band: (params.get("time_band") as TimeBand) || undefined,
      service: (params.get("service") as ServiceFilter) || undefined,
      routes: routesStr ? routesStr.split(",").filter(Boolean) : undefined,
    });
    // `params` (not a derived string key) is the dependency, matching
    // useDefaultRangeAnchor's pattern: react-router memoizes useSearchParams'
    // return value on `location.search`, so this only re-runs when the URL's
    // query string actually changes, not on every unrelated render.
  }, [agencyId, isLoading, session, params, setParams]);
}
