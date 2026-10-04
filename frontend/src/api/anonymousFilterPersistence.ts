import { useEffect, useRef } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useSession } from "./auth";
import type { DowFilter, ServiceFilter, TimeBand } from "./scope";
import { screenOf } from "./screenScope";

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

function storageKey(agencyId: number, screen: string): string {
  return `${KEY_PREFIX}${agencyId}.${screen}`;
}

function readStored(key: string): StoredFilter | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    return JSON.parse(raw) as StoredFilter;
  } catch {
    return null;
  }
}

function writeStored(key: string, value: StoredFilter): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* localStorage unavailable (private browsing, quota) — fail open, same
     * as Sidebar.tsx's readCollapsedPref/writeCollapsedPref. */
  }
}

/**
 * Anonymous-only convenience: remember the last-used date range/DOW/
 * time-band/routes filter per agency and screen in localStorage, and restore
 * it on a fresh visit to that screen that has no explicit filter params in
 * the URL. Keyed by screen for the same reason the rail is (see
 * screenScope.ts): a filter set on one screen never reappears on another.
 * A page under a screen, such as a route dossier, is left alone. This targets a
 * distinct, smaller friction than PresetMenu's login-gated named presets
 * (`/api/me/presets`): a single "remember what I was just looking at" slot
 * rather than durable, named, multi-slot filter sets —
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
  const screenName = screenOf(useLocation().pathname)?.screen ?? null;
  // Tracks which agency and screen we've already attempted a restore for, so
  // an explicit in-session reset (which clears every filter param) doesn't
  // immediately get overwritten by a re-restore of the old stored value.
  const restoredFor = useRef<string | null>(null);

  useEffect(() => {
    if (isLoading || session || agencyId == null || screenName == null) return;
    const key = storageKey(agencyId, screenName);

    const hasAnyFilterParam = Boolean(
      SCALAR_FILTER_KEYS.some((key) => params.get(key)) || params.get("routes"),
    );
    // Captured before either branch below mutates the ref: true only on the
    // very first time this hook processes this agency's screen in the session.
    const isFirstAttemptForScreen = restoredFor.current !== key;

    if (!hasAnyFilterParam && isFirstAttemptForScreen) {
      restoredFor.current = key;
      const stored = readStored(key);
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
    restoredFor.current = key;
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
    if (Object.keys(nextStored).length === 0 && !isFirstAttemptForScreen) {
      // A bare "no filter params" URL for a screen we've already processed
      // this session is ambiguous — it can mean an explicit in-session
      // clear-all, but it's also exactly what a same-agency re-navigation
      // with a dropped query string (e.g. AgencyPicker's `selectAgency`,
      // which doesn't preserve filter params) looks like. Since we can't tell those apart, never let this
      // ambiguous case silently overwrite an already-stored non-empty
      // filter; only a screen's genuine first attempt (or storage that was
      // already empty) can persist an empty object.
      const existing = readStored(key);
      if (existing && Object.keys(existing).length > 0) return;
    }
    // A link that opens a screen with only some fields (a deep link carrying
    // just `dow`, say) must not make the screen forget the remembered values
    // of the others. The current params always win for whichever field they
    // DO carry; this only fills in what's genuinely absent. `from`/`to` are
    // deliberately excluded: a link without dates asks for the rolling
    // default period, which a remembered fixed range must not replace.
    //
    // Gated to `isFirstAttemptForScreen`, same as the ambiguity guard just
    // above: beyond the first attempt, a field absent from `nextStored`
    // reflects a real, later, in-session change (e.g. the user explicitly
    // clearing just `dow` while `from`/`to`/`time_band` stay put) that must
    // be allowed to stick, not be silently undone by resurrecting the old
    // stored value.
    if (isFirstAttemptForScreen) {
      const existingForMerge = readStored(key);
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
    }
    writeStored(key, nextStored);
    // `params` (not a derived string key) is the dependency: react-router
    // memoizes useSearchParams' return value on `location.search`, so this
    // only re-runs when the URL's query string actually changes, not on every
    // unrelated render.
  }, [agencyId, screenName, isLoading, session, params, setParams]);
}
