const PIN_KEY = "transit.scopePinned";

/** Whether the scope controls sit inline under the sentence. Fails open to
 *  unpinned when storage is unavailable, like the sidebar's collapse pref. */
export function readScopePinned(): boolean {
  try {
    return localStorage.getItem(PIN_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeScopePinned(pinned: boolean): void {
  try {
    localStorage.setItem(PIN_KEY, pinned ? "1" : "0");
  } catch {
    /* ignore */
  }
}
