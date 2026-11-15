import { useSyncExternalStore } from "react";
import { readStorage, writeStorage } from "../utils/safeStorage";

const KEY = "transit.showStatistics";

const listeners = new Set<() => void>();

/** The choice where storage refuses a write, so the toggle still works until
 *  the page reloads. */
let unstored: boolean | null = null;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Whether the tables show their statistics-only columns. Off unless this
 *  browser chose otherwise: a reader meets the figures first and the
 *  statistics about them on request. */
export function readShowStatistics(): boolean {
  return unstored ?? readStorage("localStorage", KEY) === "1";
}

export function writeShowStatistics(on: boolean): void {
  unstored = writeStorage("localStorage", KEY, on ? "1" : "0") ? null : on;
  for (const listener of listeners) listener();
}

/** The setting, shared by every table and toggle on the page at once. */
export function useShowStatistics(): [boolean, (on: boolean) => void] {
  return [useSyncExternalStore(subscribe, readShowStatistics, () => false), writeShowStatistics];
}

/** Test-only: forgets a choice held in memory for a refusing store. */
export function resetShowStatisticsForTests(): void {
  unstored = null;
}
