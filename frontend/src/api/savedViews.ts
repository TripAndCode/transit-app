import { useSyncExternalStore } from "react";
import { uuid } from "../utils/uuid";
import { withStorage, writeStorage } from "../utils/safeStorage";

const KEY = "transit.savedViews.v1";

/** Mirrors the limit the server used to enforce on a saved preset's name. */
export const NAME_MAX = 120;

/** Keeps a view list short enough to scan from the menu. */
const MAX_VIEWS_PER_AGENCY = 50;

export type SavedView = {
  id: string;
  agency_id: number;
  name: string;
  range_ctx: Record<string, unknown>;
  created_at: string;
};

export type CreateSavedViewResult =
  | { ok: true; view: SavedView }
  | { ok: false; reason: "name_taken" | "full" | "save_failed" };

const listeners = new Set<() => void>();
// The parsed store, cached by the raw string it was parsed from. A render
// that hasn't seen a storage change gets the same array reference back --
// useSyncExternalStore loops otherwise. A refused read reports empty rather
// than this page's last-known value, per the stored contract below.
let cachedRaw: string | null = null;
let cached: readonly SavedView[] = [];

function isSavedView(value: unknown): value is SavedView {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.id === "string" &&
    typeof v.agency_id === "number" &&
    typeof v.name === "string" &&
    typeof v.created_at === "string" &&
    typeof v.range_ctx === "object" &&
    v.range_ctx !== null &&
    !Array.isArray(v.range_ctx)
  );
}

function parse(raw: string | null): SavedView[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    return Array.isArray(value) ? value.filter(isSavedView) : [];
  } catch {
    return [];
  }
}

/** Every stored view, reparsed only once the underlying string actually
 *  changes, so repeat reads between changes return the same array. */
function readAll(): readonly SavedView[] {
  const raw = withStorage("localStorage", (storage) => storage.getItem(KEY), null);
  if (raw !== cachedRaw) {
    cachedRaw = raw;
    cached = parse(raw);
  }
  return cached;
}

function writeAll(all: readonly SavedView[]): boolean {
  const raw = JSON.stringify(all);
  const ok = writeStorage("localStorage", KEY, raw);
  if (ok) {
    cachedRaw = raw;
    cached = all;
  }
  return ok;
}

function notify(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // A view saved in another tab lands here through the storage event; this
  // tab's own writes call notify() directly since that event never fires
  // for the tab that made the change.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function sortedForAgency(all: readonly SavedView[], agencyId: number): SavedView[] {
  return all.filter((v) => v.agency_id === agencyId).sort((a, b) => b.created_at.localeCompare(a.created_at));
}

export const savedViews = {
  /** The agency's saved views, newest first. */
  list(agencyId: number): SavedView[] {
    return sortedForAgency(readAll(), agencyId);
  },

  /** Saves `rangeCtx` under `name` for the agency. Refuses a name already
   *  used (exact match after trim) for the same agency, the 51st view for
   *  one agency, or a browser that refuses the write. */
  create(agencyId: number, name: string, rangeCtx: Record<string, unknown>): CreateSavedViewResult {
    const trimmed = name.trim().slice(0, NAME_MAX);
    const all = readAll();
    const forAgency = sortedForAgency(all, agencyId);
    if (forAgency.some((v) => v.name === trimmed)) return { ok: false, reason: "name_taken" };
    if (forAgency.length >= MAX_VIEWS_PER_AGENCY) return { ok: false, reason: "full" };
    const view: SavedView = {
      id: uuid(),
      agency_id: agencyId,
      name: trimmed,
      range_ctx: rangeCtx,
      created_at: new Date().toISOString(),
    };
    if (!writeAll([view, ...all])) return { ok: false, reason: "save_failed" };
    notify();
    return { ok: true, view };
  },

  remove(id: string): void {
    writeAll(readAll().filter((v) => v.id !== id));
    notify();
  },
};

/** The agency's saved views, newest first, re-rendering every mounted caller
 *  after a change in this tab or a `storage` event from another. */
export function useSavedViews(agencyId: number): SavedView[] {
  const all = useSyncExternalStore(subscribe, readAll, readAll);
  return sortedForAgency(all, agencyId);
}
