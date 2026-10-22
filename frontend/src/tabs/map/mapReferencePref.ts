import { readStorage, writeStorage } from "../../utils/safeStorage";

const STORAGE_KEY = "transit.opsMapReference";

/** Whether the operations map's reference panel starts expanded.
 *
 * Defaults to shown: a first-time viewer has to be told that the markers are
 * last-reported stops rather than GPS positions before they read anything
 * into where those markers sit. Once dismissed the choice sticks, because an
 * operator who has read it watches this screen for hours and re-showing it on
 * every mount would make the dismiss look like it did nothing.
 */
export function readMapReferencePref(): boolean {
  return readStorage("localStorage", STORAGE_KEY) !== "hidden";
}

export function writeMapReferencePref(open: boolean): void {
  writeStorage("localStorage", STORAGE_KEY, open ? "shown" : "hidden");
}
