import { useSyncExternalStore } from "react";

/** The routes a visitor pins for each agency, listed on the rail. Kept per
 *  browser (localStorage) rather than per account, so pins work the same
 *  signed in or not, and follow the device they were made on. */
const STORAGE_KEY = "transit.pinnedRoutes";

/** The rail lists every pin, so the list stays short enough to scan. */
export const MAX_PINNED_ROUTES = 5;

type Pins = Readonly<Record<string, readonly string[]>>;

const NO_PINS: readonly string[] = [];
const listeners = new Set<() => void>();
// The parsed store, and the localStorage text it was parsed from. When
// localStorage refuses a write, `pins` still holds it for this page.
let pins: Pins = {};
let pinsRaw: string | null = null;

function parse(raw: string | null): Pins {
  if (!raw) return {};
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    const out: Record<string, readonly string[]> = {};
    for (const [agency, codes] of Object.entries(value)) {
      if (Array.isArray(codes)) out[agency] = codes.filter((code): code is string => typeof code === "string");
    }
    return out;
  } catch {
    return {};
  }
}

function snapshot(): Pins {
  let raw: string | null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    return pins;
  }
  if (raw !== pinsRaw) {
    pinsRaw = raw;
    pins = parse(raw);
  }
  return pins;
}

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab's pin lands here through the storage event.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** Pins `code` for the agency, or unpins it when pinned. A pin past
 *  `MAX_PINNED_ROUTES` is refused rather than displacing an older one. */
export function togglePinnedRoute(agencyId: string | number, code: string): void {
  const current = snapshot();
  const key = String(agencyId);
  const list = current[key] ?? NO_PINS;
  let next: readonly string[];
  if (list.includes(code)) next = list.filter((pinned) => pinned !== code);
  else if (list.length < MAX_PINNED_ROUTES) next = [...list, code];
  else return;
  pins = { ...current, [key]: next };
  try {
    const raw = JSON.stringify(pins);
    localStorage.setItem(STORAGE_KEY, raw);
    pinsRaw = raw;
  } catch {
    /* localStorage unavailable: keep the pins for this page only. */
  }
  notify();
}

/** The agency's pins, oldest first, re-rendering on every change. */
export function usePinnedRoutes(agencyId: string | number | null): readonly string[] {
  const all = useSyncExternalStore(subscribe, snapshot, snapshot);
  return agencyId == null ? NO_PINS : (all[String(agencyId)] ?? NO_PINS);
}
