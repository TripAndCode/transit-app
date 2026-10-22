import { readStorage, removeStorage, writeStorage } from "../utils/safeStorage";

const KEY = "transit.lastAgency";

/** Read the last-chosen agency id, persisted by the onboarding overlay.
 *  Returns null when unset, invalid, or localStorage is unavailable. */
export function readLastAgency(): number | null {
  const raw = readStorage("localStorage", KEY);
  if (!raw) return null;
  const id = Number(raw);
  return Number.isInteger(id) && id > 0 ? id : null;
}

/** Persist the chosen agency id. No-ops if localStorage is unavailable. */
export function writeLastAgency(id: number): void {
  writeStorage("localStorage", KEY, String(id));
}

/** Clear the persisted last-chosen agency id, so OnboardingGate shows its
 *  agency picker on the next visit to "/" instead of returning to that
 *  agency. No-ops if localStorage is unavailable. */
export function clearLastAgency(): void {
  removeStorage("localStorage", KEY);
}
