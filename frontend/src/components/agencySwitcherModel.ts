import type { Agency } from "../api/types";

/** Where a switch lands when the current location carries no tab segment
 *  (e.g. `/agencies/7` itself). `operations` is the app's first tab, and the
 *  bare agency route redirects there anyway — naming it here avoids the
 *  extra redirect hop. */
const DEFAULT_TAB = "operations";

/**
 * The URL a switch to `agencyId` navigates to. Switching agencies is a change
 * of subject, not of view: the tab the user is reading and the filter context
 * they built stay exactly as they were, so the same question is asked of the
 * new agency.
 *
 * `ctxSuffix` is the already-built `?...` string (empty when there is none),
 * so callers keep using whatever they already derive from `ctxToQueryString`.
 */
export function buildAgencySwitchPath(agencyId: number, tab: string | undefined, ctxSuffix: string): string {
  return `/agencies/${agencyId}/${tab || DEFAULT_TAB}${ctxSuffix}`;
}

/**
 * Split the agencies payload into the recently visited ones (in visit order,
 * newest first) and everything else (in payload order). An id with no
 * matching agency is dropped rather than rendered as a blank row: the stored
 * list outlives any single agency, so it can name one that has since been
 * removed from the payload.
 */
export function orderAgenciesForSwitcher(
  agencies: Agency[],
  recentIds: number[],
): { recent: Agency[]; others: Agency[] } {
  const byId = new Map(agencies.map((a) => [a.agency_id, a]));
  const recent = recentIds.map((id) => byId.get(id)).filter((a): a is Agency => a != null);
  const recentSet = new Set(recent.map((a) => a.agency_id));
  return { recent, others: agencies.filter((a) => !recentSet.has(a.agency_id)) };
}

export type FreshnessLevel = "current" | "recent" | "stale" | "unknown";

/** A feed that landed yesterday is still the newest data there is: the daily
 *  ingest runs after the service day closes, so "one day behind" is the
 *  normal steady state, not a warning. */
const CURRENT_MAX_DAYS = 1;
const RECENT_MAX_DAYS = 7;

const MS_PER_DAY = 86_400_000;

/**
 * How old an agency's newest data is, as whole calendar days and a coarse
 * level for the switcher's pill. Compared date-to-date (both normalised to
 * UTC midnight) so a clock-time difference never rounds a same-day feed into
 * "yesterday".
 */
export function agencyFreshness(
  latestDataDate: string | null,
  now: Date = new Date(),
): { level: FreshnessLevel; days: number | null } {
  if (!latestDataDate) return { level: "unknown", days: null };
  const latest = Date.parse(`${latestDataDate}T00:00:00Z`);
  if (!Number.isFinite(latest)) return { level: "unknown", days: null };
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const days = Math.max(0, Math.floor((today - latest) / MS_PER_DAY));
  if (days <= CURRENT_MAX_DAYS) return { level: "current", days };
  if (days <= RECENT_MAX_DAYS) return { level: "recent", days };
  return { level: "stale", days };
}

/**
 * The index a type-ahead buffer should move the active option to: the first
 * name starting with the buffer, else the first containing it, else
 * `fallbackIndex` (leave the selection where it is rather than jumping
 * somewhere arbitrary on a typo).
 */
export function typeAheadIndex(names: string[], buffer: string, fallbackIndex: number): number {
  const q = buffer.toLowerCase();
  if (!q) return fallbackIndex;
  const startsWith = names.findIndex((name) => name.toLowerCase().startsWith(q));
  if (startsWith !== -1) return startsWith;
  const contains = names.findIndex((name) => name.toLowerCase().includes(q));
  return contains === -1 ? fallbackIndex : contains;
}
