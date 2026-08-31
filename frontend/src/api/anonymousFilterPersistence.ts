import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useSession } from "./auth";
import { computeAnchorRange } from "./defaultRangeAnchor";
import { useAgencies } from "./hooks";
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
 *
 * Defers entirely to `useDefaultRangeAnchor` (via the shared, pure
 * `computeAnchorRange`) whenever both would act on the same fresh visit: a
 * verified-non-empty anchored window is a correctness floor (never show a
 * guaranteed-empty default), while restoring a remembered filter is a
 * convenience on top that must not silently reintroduce the empty-view
 * problem the anchor exists to prevent. Both hooks read the same
 * already-cached `agencies` data and the same `params` at the same render,
 * so this produces a deterministic precedence without either hook needing
 * to know about the other's internal state or effect timing.
 */
export function useAnonymousFilterPersistence(agencyId: number | null): void {
  const { data: session, isLoading } = useSession();
  const [params, setParams] = useSearchParams();
  const { data: agencies } = useAgencies();
  // Tracks which agency we've already attempted a restore for, so an
  // explicit in-session reset (which clears every filter param) doesn't
  // immediately get overwritten by a re-restore of the old stored value.
  const restoredFor = useRef<number | null>(null);

  useEffect(() => {
    if (isLoading || session || agencyId == null) return;
    if (computeAnchorRange(agencyId, agencies, params)) return;

    const hasAnyFilterParam = Boolean(
      SCALAR_FILTER_KEYS.some((key) => params.get(key)) || params.get("routes"),
    );
    // Captured before either branch below mutates the ref: true only on the
    // very first time this hook processes this agency in the session.
    const isFirstAttemptForAgency = restoredFor.current !== agencyId;

    if (!hasAnyFilterParam && isFirstAttemptForAgency) {
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
    if (Object.keys(nextStored).length === 0 && !isFirstAttemptForAgency) {
      // A bare "no filter params" URL for an agency we've already processed
      // this session is ambiguous — it can mean an explicit in-session
      // clear-all, but it's also exactly what a same-agency re-navigation
      // with a dropped query string (e.g. AgencyPicker's `selectAgency`,
      // which doesn't preserve filter params the way Sidebar's nav links
      // do) looks like. Since we can't tell those apart, never let this
      // ambiguous case silently overwrite an already-stored non-empty
      // filter; only an agency's genuine first attempt (or storage that was
      // already empty) can persist an empty object.
      const existing = readStored(agencyId);
      if (existing && Object.keys(existing).length > 0) return;
    }
    // A field missing from the CURRENT params (e.g. dow/time_band/service/
    // routes when this render's URL only carries the from/to
    // useDefaultRangeAnchor just wrote, before this hook ever got a chance
    // to restore them — see this hook's deferral to computeAnchorRange
    // above) must not silently drop that field's last known stored value.
    // The current params always win for whichever field they DO carry; this
    // only fills in what's genuinely absent. `from`/`to` are deliberately
    // excluded from this merge — reviving a stale stored date range here
    // would reintroduce exactly the guaranteed-empty-view problem
    // useDefaultRangeAnchor exists to prevent.
    const existingForMerge = readStored(agencyId);
    if (existingForMerge) {
      for (const key of ["dow", "time_band", "service"] as const) {
        if (nextStored[key] === undefined && existingForMerge[key] !== undefined) {
          (nextStored as Record<string, string>)[key] = existingForMerge[key] as string;
        }
      }
      if (nextStored.routes === undefined && existingForMerge.routes && existingForMerge.routes.length > 0) {
        nextStored.routes = existingForMerge.routes;
      }
    }
    writeStored(agencyId, nextStored);
    // `params` (not a derived string key) is the dependency, matching
    // useDefaultRangeAnchor's pattern: react-router memoizes useSearchParams'
    // return value on `location.search`, so this only re-runs when the URL's
    // query string actually changes, not on every unrelated render.
  }, [agencyId, isLoading, session, params, setParams, agencies]);
}
