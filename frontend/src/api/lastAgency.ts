const KEY = "transit.lastAgency";

/** Read the last-chosen agency id, persisted by the onboarding overlay.
 *  Returns null when unset, invalid, or localStorage is unavailable. */
export function readLastAgency(): number | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const id = Number(raw);
    return Number.isInteger(id) && id > 0 ? id : null;
  } catch {
    return null;
  }
}

/** Persist the chosen agency id. No-ops if localStorage is unavailable. */
export function writeLastAgency(id: number): void {
  try {
    localStorage.setItem(KEY, String(id));
  } catch {
    /* ignore */
  }
}
