const KEY = "transit.recentAgencies";

/** How many agencies the switcher offers above the full list. Beyond a
 *  handful the "recent" group stops being a shortcut and becomes a second
 *  copy of the list. */
export const MAX_RECENT_AGENCIES = 5;

/** Most-recently-switched-to agency ids, newest first. Returns an empty list
 *  when unset, corrupt, or localStorage is unavailable — a lost shortcut
 *  list is never worth failing a render over. */
export function readRecentAgencies(): number[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((x): x is number => typeof x === "number" && Number.isInteger(x));
  } catch {
    return [];
  }
}

/** Record a switch and return the new list: `id` first, no duplicates,
 *  capped at MAX_RECENT_AGENCIES. */
export function pushRecentAgency(id: number): number[] {
  const next = [id, ...readRecentAgencies().filter((existing) => existing !== id)].slice(0, MAX_RECENT_AGENCIES);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* localStorage unavailable (private browsing, quota) — recents just don't persist */
  }
  return next;
}
