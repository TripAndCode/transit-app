import { useSyncExternalStore } from "react";
import { readStorage, writeStorage } from "../utils/safeStorage";

const KEY = "transit.showStatistics";

const listeners = new Set<() => void>();

/** The setting, read from storage once and then kept by the writer, so a
 *  render never touches storage and a refused write still switches it until
 *  the page reloads. */
let cached: boolean | null = null;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Whether the tables show their statistics-only columns. Off unless this
 *  browser chose otherwise: a reader meets the figures first and the
 *  statistics about them on request. */
export function readShowStatistics(): boolean {
  cached ??= readStorage("localStorage", KEY) === "1";
  return cached;
}

export function writeShowStatistics(on: boolean): void {
  cached = on;
  writeStorage("localStorage", KEY, on ? "1" : "0");
  for (const listener of listeners) listener();
}

/** The setting, shared by every table and toggle on the page at once. */
export function useShowStatistics(): [boolean, (on: boolean) => void] {
  return [useSyncExternalStore(subscribe, readShowStatistics, () => false), writeShowStatistics];
}

/** Test-only: forgets the cached setting, so the next read goes to storage. */
export function resetShowStatisticsForTests(): void {
  cached = null;
}
