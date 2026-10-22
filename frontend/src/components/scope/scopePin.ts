import { readStorage, writeStorage } from "../../utils/safeStorage";

const PIN_KEY = "transit.scopePinned";

/** Whether the scope controls sit inline under the sentence. Fails open to
 *  unpinned when storage is unavailable, like the sidebar's collapse pref. */
export function readScopePinned(): boolean {
  return readStorage("localStorage", PIN_KEY) === "1";
}

export function writeScopePinned(pinned: boolean): void {
  writeStorage("localStorage", PIN_KEY, pinned ? "1" : "0");
}
